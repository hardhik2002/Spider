import asyncio
import logging
import time

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.crawler.deduplicator import ContentDeduplicator, content_hash
from app.crawler.embedding import EmbeddingProvider
from app.crawler.fetcher import Fetcher, FetchError, FetchSkipped
from app.crawler.frontier import Frontier, PriorityFrontier
from app.crawler.link_scheduler import LinkScheduler
from app.crawler.models import CrawlMode, FrontierItem, JobStatus, PageStatus, RejectionReason
from app.crawler.normalizer import normalize_url
from app.crawler.parser import parse_page
from app.crawler.robots import RobotsManager
from app.crawler.scoring import ScoringFailure, SemanticScorer
from app.core.config import Settings
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
        embedding_provider: EmbeddingProvider,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.repository = repository
        self.fetcher = fetcher
        self.robots = robots
        self.embedding_provider = embedding_provider
        self.settings = settings

    async def run(self, job_id: str) -> None:
        async with self.session_factory() as session:
            job = await session.get(CrawlJob, job_id)
            if job is None:
                return
            job.status = JobStatus.RUNNING
            job.started_at = utc_now()
            await session.commit()
            logger.info("crawl started", extra={"job_id": job_id})
            started = time.monotonic()
            scorer = None
            final_status = JobStatus.COMPLETED
            error_message = None
            try:
                async with asyncio.timeout(job.timeout):
                    if job.research_query:
                        scorer = SemanticScorer(
                            self.embedding_provider,
                            depth_penalty=job.depth_penalty,
                            max_context_chars=self.settings.max_candidate_context_chars,
                            max_page_chars=self.settings.max_page_scoring_chars,
                        )
                        if job.crawl_mode == CrawlMode.INTELLIGENT:
                            logger.info("intelligent crawl started", extra={"job_id": job.id})
                        await scorer.prepare(job.research_query)
                        logger.info(
                            "research query embedded in %s ms",
                            scorer.query_embedding_ms,
                            extra={"job_id": job.id},
                        )
                    await self._crawl(session, job, scorer)
                logger.info("crawl completed", extra={"job_id": job_id})
                if job.crawl_mode == CrawlMode.INTELLIGENT:
                    logger.info("intelligent crawl completed", extra={"job_id": job_id})
            except asyncio.CancelledError:
                final_status = JobStatus.CANCELLED
                error_message = "Crawl task cancelled"
                logger.info("crawl cancelled", extra={"job_id": job_id})
            except Exception as exc:
                final_status = JobStatus.FAILED
                error_message = f"{type(exc).__name__}: {exc}"[:1000]
                logger.exception("crawl failed", extra={"job_id": job_id})
            finally:
                await session.rollback()
                async with self.session_factory() as final_session:
                    persisted = await final_session.get(CrawlJob, job_id)
                    persisted.status = final_status
                    persisted.error_message = error_message
                    if scorer is not None:
                        persisted.query_embedding_ms = scorer.query_embedding_ms
                        persisted.candidate_embeddings = scorer.candidate_count
                        persisted.candidate_scoring_ms = scorer.candidate_scoring_ms
                        persisted.model_load_ms = self.embedding_provider.load_time_ms
                    persisted.duration_ms = int((time.monotonic() - started) * 1000)
                    persisted.completed_at = utc_now()
                    await final_session.commit()

    async def _crawl(self, session, job: CrawlJob, scorer: SemanticScorer | None) -> None:
        frontier = (
            PriorityFrontier(job.exploration_rate)
            if job.crawl_mode == CrawlMode.INTELLIGENT
            else Frontier()
        )
        frontier.push(FrontierItem(job.start_url, job.start_url, 0))
        scheduler = LinkScheduler(job, frontier, scorer)
        job.pages_discovered = 1
        deduplicator = ContentDeduplicator(await self.repository.existing_hashes(session, job.id))
        attempts = 0

        async def check_redirect(url: str) -> None:
            if not scheduler.in_scope(url):
                raise FetchSkipped("Redirect outside crawl scope")
            if not await self.robots.allowed(url):
                raise FetchSkipped("Redirect denied by robots.txt")

        while frontier and attempts < job.max_pages:
            item = frontier.pop()
            if isinstance(frontier, PriorityFrontier):
                logger.info(
                    "priority frontier pop: %s", item.normalized_url,
                    extra={"job_id": job.id},
                )
                if frontier.last_pop_exploration:
                    logger.info("exploration selection", extra={"job_id": job.id})
            attempts += 1
            page = await self.repository.add_page(session, job.id, item)
            try:
                if not await self.robots.allowed(item.normalized_url):
                    page.crawl_status = PageStatus.SKIPPED
                    page.error_message = "Denied by robots.txt"
                    job.pages_skipped += 1
                    await scheduler.reject_selected(
                        session, item, RejectionReason.ROBOTS_DENIED
                    )
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
                parsed = parse_page(result.body, page.final_url, scheduler.root_domain)
                page.crawl_status = PageStatus.PARSED
                page.title = parsed.title
                page.meta_description = parsed.meta_description
                page.canonical_url = parsed.canonical_url
                digest = content_hash(parsed.text_content)
                page.content_hash = digest
                if scorer is not None:
                    try:
                        page.actual_page_relevance = await scorer.score_page(
                            parsed.title, parsed.meta_description, parsed.text_content
                        )
                    except Exception as exc:
                        raise ScoringFailure("Page embedding or scoring failed") from exc
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
                link_rows = await self.repository.add_links(session, job.id, page, parsed.links)
                await scheduler.schedule(session, page, link_rows)
            except FetchSkipped as exc:
                page.crawl_status = PageStatus.SKIPPED
                page.error_message = str(exc)
                job.pages_skipped += 1
                reason = (
                    RejectionReason.ROBOTS_DENIED
                    if "robots" in str(exc).lower()
                    else RejectionReason.EXTERNAL_DOMAIN_DISABLED
                )
                await scheduler.reject_selected(session, item, reason)
                logger.info("URL skipped: %s", item.normalized_url, extra={"job_id": job.id})
            except ScoringFailure:
                raise
            except (FetchError, ValueError) as exc:
                page.crawl_status = PageStatus.FAILED
                page.error_message = str(exc)[:1000]
                if isinstance(exc, FetchError):
                    page.status_code = exc.status_code
                job.pages_failed += 1
                logger.warning(
                    "URL failed: %s: %s", item.normalized_url, exc, extra={"job_id": job.id}
                )
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
            await scheduler.reject_selected(session, item, RejectionReason.PAGE_LIMIT)
        await session.commit()
