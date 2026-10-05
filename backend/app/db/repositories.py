import json

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crawler.models import JobStatus, PageStatus
from app.db.models import CrawlJob, CrawledPage, DiscoveredLink, utc_now
from app.schemas.crawl import CrawlRequest


class CrawlRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def create_job(self, request: CrawlRequest, start_url: str) -> CrawlJob:
        async with self.session_factory() as session:
            job = CrawlJob(
                start_url=start_url,
                max_depth=request.max_depth,
                max_pages=request.max_pages,
                timeout=request.timeout,
                max_content_size=request.max_content_size,
                allowed_domains=json.dumps(request.allowed_domains),
                allow_external_domains=request.allow_external_domains,
            )
            session.add(job)
            await session.commit()
            return job

    async def get_job(self, job_id: str) -> CrawlJob | None:
        async with self.session_factory() as session:
            return await session.get(CrawlJob, job_id)

    async def recover_jobs(self) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(CrawlJob)
                .where(CrawlJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]))
                .values(
                    status=JobStatus.FAILED,
                    completed_at=utc_now(),
                    error_message="Application restarted before crawl completed",
                )
            )
            await session.commit()

    async def add_page(self, session: AsyncSession, job_id: str, item) -> CrawledPage:
        page = CrawledPage(
            crawl_job_id=job_id,
            url=item.url,
            normalized_url=item.normalized_url,
            parent_url=item.parent_url,
            depth=item.depth,
            crawl_status=PageStatus.QUEUED,
        )
        session.add(page)
        await session.commit()
        return page

    async def add_links(
        self, session: AsyncSession, job_id: str, page: CrawledPage, links
    ) -> None:
        session.add_all(
            DiscoveredLink(
                crawl_job_id=job_id,
                source_page_id=page.id,
                source_url=page.final_url or page.url,
                target_url=link.target_url,
                normalized_target_url=link.normalized_target_url,
                anchor_text=link.anchor_text,
                is_internal=link.is_internal,
            )
            for link in links
        )
        await session.commit()

    async def existing_hashes(self, session: AsyncSession, job_id: str) -> dict[str, int]:
        rows = await session.execute(
            select(CrawledPage.content_hash, CrawledPage.id).where(
                CrawledPage.crawl_job_id == job_id,
                CrawledPage.content_hash.is_not(None),
                CrawledPage.duplicate_of_page_id.is_(None),
            )
        )
        return {digest: page_id for digest, page_id in rows}
