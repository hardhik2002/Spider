from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.db.models import ResearchJob
from app.rag.consistency import check_consistency
from app.rag.models import IndexDocumentStatus, IndexJob
from app.schemas.rag import RetrievalRequest

router = APIRouter(prefix="/api/v1/research", tags=["rag"])


async def _ensure_research(request: Request, job_id: UUID) -> None:
    async with request.app.state.index_service.sessions() as session:
        if await session.get(ResearchJob, str(job_id)) is None:
            raise HTTPException(404, "Research job not found")


@router.post("/{job_id}/index", status_code=status.HTTP_202_ACCEPTED)
async def start_index(job_id: UUID, request: Request) -> dict:
    await _ensure_research(request, job_id)
    row = await request.app.state.index_service.start(str(job_id))
    return {"index_job_id": row.id, "status": row.status.lower()}


@router.get("/{job_id}/index")
async def index_status(job_id: UUID, request: Request) -> dict:
    await _ensure_research(request, job_id)
    async with request.app.state.index_service.sessions() as session:
        row = (
            (
                await session.execute(
                    select(IndexJob)
                    .where(IndexJob.research_job_id == str(job_id))
                    .order_by(IndexJob.created_at.desc(), IndexJob.id.desc())
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return {"research_job_id": str(job_id), "status": "NOT_INDEXED"}
        failures = (
            (
                await session.execute(
                    select(IndexDocumentStatus).where(
                        IndexDocumentStatus.index_job_id == row.id,
                        IndexDocumentStatus.status == "FAILED",
                    )
                )
            )
            .scalars()
            .all()
        )
        keys = (
            "id",
            "research_job_id",
            "status",
            "created_at",
            "started_at",
            "completed_at",
            "documents_discovered",
            "documents_processed",
            "documents_indexed",
            "documents_skipped",
            "documents_failed",
            "chunks_created",
            "chunks_skipped",
            "chunks_deduplicated",
            "chunks_embedded",
            "vector_records",
            "lexical_records",
            "average_chunk_tokens",
            "median_chunk_tokens",
            "max_chunk_tokens",
            "embedding_batches",
            "embedding_duration_ms",
            "vector_upsert_duration_ms",
            "fts_index_duration_ms",
            "duration_ms",
            "embedding_model",
            "embedding_dimension",
            "chunking_profile",
            "error_message",
        )
        result = {key: getattr(row, key) for key in keys}
        result["index_job_id"] = result.pop("id")
        result["errors"] = [
            {"crawled_page_id": x.crawled_page_id, "message": x.error_message} for x in failures
        ]
        return result


@router.get("/{job_id}/index/consistency")
async def index_consistency(job_id: UUID, request: Request) -> dict:
    await _ensure_research(request, job_id)
    service = request.app.state.index_service
    return await check_consistency(service.sessions, service.vectors, str(job_id))


@router.post("/{job_id}/retrieve")
async def retrieve(job_id: UUID, payload: RetrievalRequest, request: Request) -> dict:
    await _ensure_research(request, job_id)
    try:
        return await request.app.state.retrieval_service.retrieve(str(job_id), payload)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Retrieval failed: {exc}") from exc
