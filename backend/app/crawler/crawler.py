import asyncio
import json
import logging
import time

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.crawler.deduplicator import ContentDeduplicator, content_hash
from app.crawler.fetcher import FetchError, FetchSkipped, Fetcher
from app.crawler.frontier import Frontier
from app.crawler.models import FrontierItem, JobStatus, PageStatus
from app.crawler.normalizer import hostname, normalize_url
from app.crawler.parser import parse_page
from app.crawler.robots import RobotsManager
from app.db.models import CrawlJob, utc_now
from app.db.repositories import CrawlRepository

logger = logging.getLogger("spidermind.crawler")


class Crawler:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        repository: CrawlRepository,
        fetcher: Fetcher,
        robots: RobotsManager,
    ) -> None:
        self.session_factory = session_factory
        self.repository = repository
        self.fetcher = fetcher
        self.robots = robots

    async def run(self, job_id: str) -> None:
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            if job is None:
                return
            job.status = JobStatus.RUNNING
            job.started_at = utc_now()
            await session.commit()
            logger.info("crawl started", extra={"job_id": job_id})
            try:
                async with asyncio.timeout(job.timeout):
                    await self._crawl(session, job)
                job.status = JobStatus.COMPLETED
                logger.info("crawl completed", extra={"job_id": job_id})
            except asyncio.CancelledError:
                job.status = JobStatus.CANCELLED
                job.error_message = "Crawl task cancelled"
                logger.info("crawl cancelled", extra={"job_id": job_id})
            except Exception as exc:
                job.status = JobStatus.FAILED
                job.error_message = f"{type(exc).__name__}: {exc}"[:1000]
                logger.exception("crawl failed", extra={"job_id": job_id})
            finally:
                job.completed_at = utc_now()
                await session.commit()

    async def _crawl(self, session, job: CrawlJob) -> None:
        root_domain = hostname(job.start_url)
        allowed_domains = set(json.loads(job.allowed_domains))
        if allowed_domains and root_domain not in allowed_domains:
            raise ValueError("Start domain is not included in allowed_domains")
        frontier = Frontier()
        frontier.push(FrontierItem(job.start_url, job.start_url, 0))
        seen = {job.start_url}
        job.pages_discovered = 1
        deduplicator = ContentDeduplicator()
        deduplicator._hashes.update(await self.repository.existing_hashes(session, job.id))
        attempts = 0

        def in_scope(url: str) -> bool:
            domain = hostname(url)
            return (domain == root_domain or job.allow_external_domains) and (
                not allowed_domains or domain in allowed_domains
            )

        async def check_redirect(url: str) -> None:
            if not in_scope(url):
                raise FetchSkipped("Redirect outside crawl scope")
            if not await self.robots.allowed(url):
                raise FetchSkipped("Redirect denied by robots.txt")

        while frontier and attempts < job.max_pages:
            item = frontier.pop()
            attempts += 1
            page = await self.repository.add_page(session, job.id, item)
            try:
                if not await self.robots.allowed(item.normalized_url):
                    page.crawl_status = PageStatus.SKIPPED
                    page.error_message = "Denied by robots.txt"
                    job.pages_skipped += 1
                    logger.info("robots denied", extra={"job_id": job.id})
                    continue
                page.crawl_status = PageStatus.FETCHING
                await session.commit()
                logger.info("fetching URL: %s", item.normalized_url, extra={"job_id": job.id})
                result = await self.fetcher.fetch(
                    item.normalized_url, job.max_content_size, redirect_allowed=check_redirect
                )
                page.final_url = normalize_url(result.final_url)
                page.status_code = result.status_code
                page.content_type = result.content_type
                page.response_size = result.response_size
                page.response_time_ms = result.response_time_ms
                logger.info("fetch completed", extra={"job_id": job.id})
                parsed = parse_page(result.body, page.final_url, root_domain)
                page.crawl_status = PageStatus.PARSED
                page.title = parsed.title
                page.meta_description = parsed.meta_description
                page.canonical_url = parsed.canonical_url
                digest = content_hash(parsed.text_content)
                page.content_hash = digest
                duplicate_id = deduplicator.find_or_add(digest, page.id)
                if duplicate_id is not None:
                    page.duplicate_of_page_id = duplicate_id
                    page.crawl_status = PageStatus.SKIPPED
                    page.error_message = "Duplicate extracted content"
                    job.pages_skipped += 1
                    logger.info("duplicate detected", extra={"job_id": job.id})
                else:
                    page.text_content = parsed.text_content
                    page.crawl_status = PageStatus.COMPLETED
                    job.pages_crawled += 1
                page.crawled_at = utc_now()
                await session.commit()
                logger.info("parsing completed", extra={"job_id": job.id})
                await self.repository.add_links(session, job.id, page, parsed.links)
                for link in parsed.links:
                    target = link.normalized_target_url
                    if target in seen:
                        continue
                    seen.add(target)
                    job.pages_discovered += 1
                    logger.info("URL discovered: %s", target, extra={"job_id": job.id})
                    if item.depth + 1 > job.max_depth or not in_scope(target):
                        job.pages_skipped += 1
                        logger.info("URL skipped: %s", target, extra={"job_id": job.id})
                        continue
                    frontier.push(
                        FrontierItem(link.target_url, target, item.depth + 1, page.final_url)
                    )
            except FetchSkipped as exc:
                page.crawl_status = PageStatus.SKIPPED
                page.error_message = str(exc)
                job.pages_skipped += 1
                logger.info("URL skipped: %s", item.normalized_url, extra={"job_id": job.id})
            except (FetchError, ValueError) as exc:
                page.crawl_status = PageStatus.FAILED
                page.error_message = str(exc)[:1000]
                if isinstance(exc, FetchError):
                    page.status_code = exc.status_code
                job.pages_failed += 1
                logger.warning("URL failed: %s: %s", item.normalized_url, exc, extra={"job_id": job.id})
            except Exception:
                page.crawl_status = PageStatus.FAILED
                page.error_message = "Unexpected page error"
                job.pages_failed += 1
                logger.exception("URL failed: %s", item.normalized_url, extra={"job_id": job.id})
            finally:
                page.crawled_at = page.crawled_at or utc_now()
                await session.commit()
        # Remaining in-scope URLs were discovered but never fetched because of max_pages.
        while frontier:
            item = frontier.pop()
            page = await self.repository.add_page(session, job.id, item)
            page.crawl_status = PageStatus.SKIPPED
            page.error_message = "max_pages reached"
            page.crawled_at = utc_now()
            job.pages_skipped += 1
        await session.commit()
