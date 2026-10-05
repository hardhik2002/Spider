import asyncio
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

import httpx

from app.crawler.models import FetchResult
from app.crawler.normalizer import InvalidURL, normalize_url
from app.crawler.rate_limit import DomainRateLimiter
from app.crawler.security import TargetValidator, UnsafeTarget


class FetchError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FetchSkipped(FetchError):
    pass


class Fetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        validator: TargetValidator,
        limiter: DomainRateLimiter,
        *,
        timeout: float,
        max_redirects: int,
        max_retries: int,
    ) -> None:
        self.client = client
        self.validator = validator
        self.limiter = limiter
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.max_retries = max_retries

    async def fetch(
        self,
        url: str,
        max_size: int,
        *,
        robots: bool = False,
        redirect_allowed: Callable[[str], Awaitable[None]] | None = None,
    ) -> FetchResult:
        started = time.monotonic()
        current = url
        for redirect_count in range(self.max_redirects + 1):
            try:
                current = await self.validator.validate(current)
            except (InvalidURL, UnsafeTarget) as exc:
                raise FetchError(str(exc)) from exc
            for attempt in range(self.max_retries + 1):
                await self.limiter.wait(current)
                try:
                    async with self.client.stream(
                        "GET", current, follow_redirects=False, timeout=self.timeout
                    ) as response:
                        status = response.status_code
                        if status in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise FetchError("Redirect without Location", status)
                            try:
                                current = normalize_url(urljoin(current, location))
                            except InvalidURL as exc:
                                raise FetchError("Invalid redirect target", status) from exc
                            if redirect_allowed:
                                await redirect_allowed(current)
                            break
                        if status in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                            await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                            continue
                        if status >= 400:
                            raise FetchError(f"HTTP {status}", status)
                        content_type = (
                            response.headers.get("content-type", "").split(";")[0].lower()
                        )
                        allowed = (
                            {"text/plain"} if robots else {"text/html", "application/xhtml+xml"}
                        )
                        if content_type not in allowed:
                            raise FetchError(
                                f"Unsupported content type: {content_type or 'missing'}", status
                            )
                        length = response.headers.get("content-length")
                        if length and length.isdigit() and int(length) > max_size:
                            raise FetchError("Response exceeds maximum size", status)
                        chunks = bytearray()
                        async for chunk in response.aiter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) > max_size:
                                raise FetchError("Response exceeds maximum size", status)
                        return FetchResult(
                            final_url=str(response.url),
                            status_code=status,
                            content_type=content_type,
                            response_time_ms=int((time.monotonic() - started) * 1000),
                            response_size=len(chunks),
                            body=bytes(chunks),
                        )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt == self.max_retries:
                        raise FetchError(f"Network error: {type(exc).__name__}") from exc
                    await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
            else:
                raise FetchError("Retry limit exceeded")
            if redirect_count == self.max_redirects:
                raise FetchError("Redirect limit exceeded")
        raise FetchError("Redirect limit exceeded")
