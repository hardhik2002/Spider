import asyncio
import time

from app.crawler.normalizer import hostname


class DomainRateLimiter:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self._next_allowed: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, url: str) -> None:
        domain = hostname(url)
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            delay = self._next_allowed.get(domain, 0) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_allowed[domain] = time.monotonic() + self.delay_seconds
