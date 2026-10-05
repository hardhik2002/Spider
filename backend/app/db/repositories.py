import json

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crawler.models import JobStatus, PageStatus
from app.core.config import Settings
from app.db.models import CrawledPage, CrawlJob, DiscoveredLink, utc_now
from app.schemas.crawl import CrawlRequest


class CrawlRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def create_job(
        self, request: CrawlRequest, start_url: str, settings: Settings, model_name: str
    ) -> CrawlJob:
        async with self.session_factory() as session:
            job = CrawlJob(
                start_url=start_url,
                max_depth=request.max_depth,
                max_pages=request.max_pages,
                timeout=request.timeout,
                max_content_size=request.max_content_size,
                allowed_domains=json.dumps(request.allowed_domains),
                allow_external_domains=request.allow_external_domains,
                research_query=request.research_query,
                crawl_mode=request.crawl_mode,
                min_relevance_score=(
                    request.min_relevance_score
                    if request.min_relevance_score is not None
                    else settings.default_min_relevance_score
                ),
                embedding_model=(model_name if request.research_query else None),
                depth_penalty=(
                    request.depth_penalty
                    if request.depth_penalty is not None
                    else settings.depth_penalty
                ),
                exploration_rate=(
                    request.exploration_rate
                    if request.exploration_rate is not None
                    else settings.exploration_rate
                ),
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
            predicted_link_relevance=item.relevance_score,
            priority_score=item.priority_score,
            discovery_order=item.discovery_order,
            source_link_id=item.source_link_id,
        )
        session.add(page)
        await session.commit()
        return page

    async def add_links(
        self, session: AsyncSession, job_id: str, page: CrawledPage, links
    ) -> list[DiscoveredLink]:
        rows = [
            DiscoveredLink(
                crawl_job_id=job_id,
                source_page_id=page.id,
                source_url=page.final_url or page.url,
                target_url=link.target_url,
                normalized_target_url=link.normalized_target_url,
                anchor_text=link.anchor_text,
                is_internal=link.is_internal,
                source_page_title=page.title,
                link_context=link.surrounding_text,
                target_depth=page.depth + 1,
            )
            for link in links
        ]
        session.add_all(rows)
        await session.commit()
        return rows

    async def list_links(
        self,
        job_id: str,
        *,
        order: str,
        selection: str,
        internal: bool | None,
        limit: int,
        offset: int,
    ) -> list[DiscoveredLink]:
        query = select(DiscoveredLink).where(DiscoveredLink.crawl_job_id == job_id)
        if selection == "selected":
            query = query.where(DiscoveredLink.selected_for_crawl.is_(True))
        elif selection == "rejected":
            query = query.where(DiscoveredLink.rejection_reason.is_not(None))
        if internal is not None:
            query = query.where(DiscoveredLink.is_internal.is_(internal))
        if order == "relevance":
            query = query.order_by(
                DiscoveredLink.relevance_score.desc().nulls_last(), DiscoveredLink.id.asc()
            )
        else:
            query = query.order_by(DiscoveredLink.id.asc())
        async with self.session_factory() as session:
            return list((await session.execute(query.limit(limit).offset(offset))).scalars())

    async def existing_hashes(self, session: AsyncSession, job_id: str) -> dict[str, int]:
        rows = await session.execute(
            select(CrawledPage.content_hash, CrawledPage.id).where(
                CrawledPage.crawl_job_id == job_id,
                CrawledPage.content_hash.is_not(None),
                CrawledPage.duplicate_of_page_id.is_(None),
            )
        )
        return {digest: page_id for digest, page_id in rows}
