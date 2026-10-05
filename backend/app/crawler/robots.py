import asyncio
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from app.crawler.fetcher import Fetcher, FetchError


class RobotsManager:
    def __init__(self, fetcher: Fetcher, user_agent: str) -> None:
        self.fetcher = fetcher
        self.user_agent = user_agent
        self._cache: dict[str, RobotFileParser | None | bool] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._cache:
                robots_url = origin + "/robots.txt"
                try:
                    result = await self.fetcher.fetch(robots_url, 256_000, robots=True)
                except FetchError as exc:
                    if exc.status_code in {404, 410}:
                        self._cache[origin] = None
                    else:
                        # Unavailable or malformed policy: fail closed.
                        self._cache[origin] = False
                else:
                    parser = RobotFileParser()
                    parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
                    self._cache[origin] = parser
        rules = self._cache[origin]
        if rules is False:
            return False
        return rules.can_fetch(self.user_agent, url) if rules else True
