import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.crawler.crawler import Crawler
from app.crawler.fetcher import Fetcher
from app.crawler.normalizer import InvalidURL, normalize_url
from app.crawler.rate_limit import DomainRateLimiter
from app.crawler.robots import RobotsManager
from app.crawler.security import TargetValidator, UnsafeTarget
from app.db.repositories import CrawlRepository
from app.schemas.crawl import CrawlRequest

logger = logging.getLogger("spidermind.service")


class CrawlService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings,
        client: httpx.AsyncClient,
        validator: TargetValidator | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.client = client
        self.validator = validator or TargetValidator()
        self.repository = CrawlRepository(session_factory)
        self.tasks: set[asyncio.Task] = set()

    async def start(self, request: CrawlRequest):
        start_url = normalize_url(request.start_url)
        await self.validator.validate(start_url)
        if request.allowed_domains and start_url.split("/")[2] not in request.allowed_domains:
            raise ValueError("Start domain must be in allowed_domains")
        job = await self.repository.create_job(request, start_url)
        task = asyncio.create_task(self._run(job.id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job

    async def _run(self, job_id: str) -> None:
        limiter = DomainRateLimiter(self.settings.domain_delay_seconds)
        fetcher = Fetcher(
            self.client,
            self.validator,
            limiter,
            timeout=self.settings.request_timeout_seconds,
            max_redirects=self.settings.max_redirects,
            max_retries=self.settings.max_retries,
        )
        robots = RobotsManager(fetcher, self.settings.user_agent)
        crawler = Crawler(self.session_factory, self.repository, fetcher, robots)
        await crawler.run(job_id)

    async def shutdown(self) -> None:
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
