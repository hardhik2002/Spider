import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.db.models import (
    CrawledPage,
    CrawlJob,
    ResearchJob,
    ResearchQuerySubquestion,
    ResearchResultOccurrence,
    ResearchSearchQuery,
    ResearchSearchResult,
    ResearchSeed,
    ResearchSubquestion,
)
from app.schemas.research import ResearchRequest

router = APIRouter(prefix="/api/v1/research", tags=["research"])


async def _job(request: Request, job_id: UUID) -> ResearchJob:
    async with request.app.state.research_service.session_factory() as session:
        job = await session.get(ResearchJob, str(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Research job not found")
    return job


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_research(payload: ResearchRequest, request: Request) -> dict:
    job = await request.app.state.research_service.start(payload)
    return {"research_job_id": job.id, "status": job.status}


@router.get("/{job_id}")
async def get_research(job_id: UUID, request: Request) -> dict:
    job = await _job(request, job_id)
    return {
        "research_job_id": job.id,
        "question": job.question,
        "normalized_question": job.normalized_question,
        "objective": job.objective,
        "status": job.status,
        "stage": job.stage,
        "planner_provider": job.planner_provider,
        "planner_model": job.planner_model,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "max_subquestions": job.max_subquestions,
        "max_total_pages": job.max_total_pages,
        "total_search_queries": job.total_search_queries,
        "total_search_results": job.total_search_results,
        "unique_search_results": job.unique_search_results,
        "selected_seed_count": job.selected_seed_count,
        "pages_crawled": job.pages_crawled,
        "failed_subquestions": job.failed_subquestions,
        "error_message": job.error_message,
    }


@router.get("/{job_id}/plan")
async def get_plan(job_id: UUID, request: Request) -> dict:
    job = await _job(request, job_id)
    async with request.app.state.research_service.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(ResearchSubquestion)
                    .where(ResearchSubquestion.research_job_id == job.id)
                    .order_by(ResearchSubquestion.order)
                )
            )
            .scalars()
            .all()
        )
    return {
        "research_job_id": job.id,
        "status": job.status,
        "plan": json.loads(job.plan_json) if job.plan_json else None,
        "subquestion_progress": [
            {"id": row.plan_id, "status": row.status, "error_message": row.error_message}
            for row in rows
        ],
    }


@router.get("/{job_id}/searches")
async def get_searches(
    job_id: UUID,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    job = await _job(request, job_id)
    async with request.app.state.research_service.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(ResearchSearchQuery)
                    .where(ResearchSearchQuery.research_job_id == job.id)
                    .order_by(ResearchSearchQuery.id)
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        output = []
        for row in rows:
            associations = (
                await session.execute(
                    select(ResearchQuerySubquestion, ResearchSubquestion)
                    .join(
                        ResearchSubquestion,
                        ResearchQuerySubquestion.subquestion_id == ResearchSubquestion.id,
                    )
                    .where(ResearchQuerySubquestion.query_id == row.id)
                )
            ).all()
            occurrences = (
                await session.execute(
                    select(ResearchResultOccurrence, ResearchSearchResult)
                    .join(
                        ResearchSearchResult,
                        ResearchResultOccurrence.result_id == ResearchSearchResult.id,
                    )
                    .where(ResearchResultOccurrence.query_id == row.id)
                    .order_by(ResearchResultOccurrence.rank)
                )
            ).all()
            output.append(
                {
                    "id": row.id,
                    "query": row.query,
                    "status": row.status,
                    "result_count": row.result_count,
                    "error_message": row.error_message,
                    "started_at": row.started_at,
                    "completed_at": row.completed_at,
                    "subquestions": [
                        {"id": sub.plan_id, "question": sub.question, "intent": link.intent}
                        for link, sub in associations
                    ],
                    "results": [
                        {
                            "id": result.id,
                            "url": result.normalized_url,
                            "title": result.title,
                            "snippet": result.snippet,
                            "rank": occurrence.rank,
                            "provider": result.provider,
                        }
                        for occurrence, result in occurrences
                    ],
                }
            )
    return {"research_job_id": job.id, "total_queries": job.total_search_queries, "queries": output}


@router.get("/{job_id}/sources")
async def get_sources(
    job_id: UUID,
    request: Request,
    selected: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    job = await _job(request, job_id)
    async with request.app.state.research_service.session_factory() as session:
        statement = (
            select(ResearchSeed, ResearchSearchResult, ResearchSubquestion)
            .join(ResearchSearchResult, ResearchSeed.result_id == ResearchSearchResult.id)
            .join(ResearchSubquestion, ResearchSeed.subquestion_id == ResearchSubquestion.id)
            .where(ResearchSeed.research_job_id == job.id)
            .order_by(ResearchSubquestion.order, ResearchSeed.seed_score.desc(), ResearchSeed.id)
        )
        if selected is not None:
            statement = statement.where(ResearchSeed.selected.is_(selected))
        rows = (await session.execute(statement.limit(limit).offset(offset))).all()
        output = []
        for seed, result, sub in rows:
            crawl = await session.get(CrawlJob, seed.crawl_job_id) if seed.crawl_job_id else None
            pages = []
            if crawl:
                page_rows = (
                    (
                        await session.execute(
                            select(CrawledPage)
                            .where(CrawledPage.crawl_job_id == crawl.id)
                            .order_by(CrawledPage.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                pages = [
                    {
                        "id": page.id,
                        "url": page.normalized_url,
                        "status": page.crawl_status,
                        "actual_page_relevance": page.actual_page_relevance,
                    }
                    for page in page_rows
                ]
            output.append(
                {
                    "seed_id": seed.id,
                    "subquestion_id": sub.plan_id,
                    "subquestion": sub.question,
                    "result_id": result.id,
                    "url": result.normalized_url,
                    "title": result.title,
                    "snippet": result.snippet,
                    "domain": result.domain,
                    "provider": result.provider,
                    "best_rank": result.best_rank,
                    "semantic_relevance": seed.semantic_relevance,
                    "seed_score": seed.seed_score,
                    "selected": seed.selected,
                    "rejection_reason": seed.rejection_reason,
                    "crawl_job_id": seed.crawl_job_id,
                    "crawl_status": crawl.status if crawl else None,
                    "pages": pages,
                }
            )
    return {"research_job_id": job.id, "sources": output}
