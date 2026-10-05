from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from app.crawler.normalizer import InvalidURL
from app.crawler.security import UnsafeTarget
from app.schemas.crawl import CrawlCreated, CrawlRequest, CrawlStatus

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
    )
