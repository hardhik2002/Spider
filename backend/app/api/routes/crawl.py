from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.crawler.normalizer import InvalidURL
from app.crawler.security import UnsafeTarget
from app.schemas.crawl import CrawlCreated, CrawlRequest, CrawlStatus, LinkInspection

router = APIRouter(prefix="/api/v1/crawl", tags=["crawl"])


@router.post("", response_model=CrawlCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_crawl(payload: CrawlRequest, request: Request) -> CrawlCreated:
    service = request.app.state.crawl_service
    try:
        job = await service.start(payload)
    except (InvalidURL, UnsafeTarget, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CrawlCreated(job_id=job.id, status=job.status)


@router.get("/{job_id}", response_model=CrawlStatus)
async def get_crawl(job_id: UUID, request: Request) -> CrawlStatus:
    job = await request.app.state.crawl_service.repository.get_job(str(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")
    return CrawlStatus(
        job_id=job.id,
        status=job.status,
        pages_discovered=job.pages_discovered,
        pages_crawled=job.pages_crawled,
        pages_failed=job.pages_failed,
        pages_skipped=job.pages_skipped,
        error_message=job.error_message,
        crawl_mode=job.crawl_mode,
        research_query=job.research_query,
        links_scored=job.links_scored if job.research_query else None,
        links_below_threshold=job.links_below_threshold if job.crawl_mode == "intelligent" else None,
        average_relevance_score=(
            job.relevance_score_sum / job.links_scored if job.links_scored else None
        ),
        highest_relevance_score=job.highest_relevance_score,
        embedding_model=job.embedding_model,
        candidate_embeddings=job.candidate_embeddings if job.research_query else None,
        query_embedding_ms=job.query_embedding_ms,
        candidate_scoring_ms=job.candidate_scoring_ms if job.research_query else None,
        model_load_ms=job.model_load_ms,
        duration_ms=job.duration_ms,
    )


@router.get("/{job_id}/links", response_model=list[LinkInspection])
async def get_crawl_links(
    job_id: UUID,
    request: Request,
    order: Literal["discovered", "relevance"] = "relevance",
    selection: Literal["all", "selected", "rejected"] = "all",
    internal: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[LinkInspection]:
    repository = request.app.state.crawl_service.repository
    if await repository.get_job(str(job_id)) is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")
    rows = await repository.list_links(
        str(job_id),
        order=order,
        selection=selection,
        internal=internal,
        limit=limit,
        offset=offset,
    )
    return [
        LinkInspection(
            id=row.id,
            source_url=row.source_url,
            url=row.target_url,
            normalized_url=row.normalized_target_url,
            anchor_text=row.anchor_text,
            source_page_title=row.source_page_title,
            link_context=row.link_context,
            is_internal=row.is_internal,
            target_depth=row.target_depth,
            discovery_order=row.discovery_order,
            relevance_score=row.relevance_score,
            priority_score=row.priority_score,
            depth_penalty=row.depth_penalty,
            scoring_status=row.scoring_status,
            scoring_reason=row.scoring_reason,
            selected_for_crawl=row.selected_for_crawl,
            rejection_reason=row.rejection_reason,
        )
        for row in rows
    ]
