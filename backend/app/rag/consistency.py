"""Cross-store consistency audit for one research job."""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.rag.models import KnowledgeChunk, KnowledgeChunkSource, KnowledgeDocument
from app.rag.vector import VectorIndex


async def check_consistency(
    sessions: async_sessionmaker, vectors: VectorIndex, research_job_id: str
) -> dict:
    async with sessions() as session:
        chunks = (
            (
                await session.execute(
                    select(KnowledgeChunk).where(KnowledgeChunk.research_job_id == research_job_id)
                )
            )
            .scalars()
            .all()
        )
        active_ids = set(
            (
                await session.execute(
                    select(KnowledgeChunkSource.chunk_id)
                    .join(
                        KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunkSource.document_id
                    )
                    .where(
                        KnowledgeChunkSource.research_job_id == research_job_id,
                        KnowledgeDocument.index_status == "COMPLETED",
                    )
                )
            )
            .scalars()
            .all()
        )
        fts_available = True
        fts_error = None
        fts_records = None
        try:
            fts_records = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM knowledge_chunks_fts f "
                        "JOIN knowledge_chunks c ON c.id = f.rowid "
                        "WHERE c.research_job_id = :job_id"
                    ),
                    {"job_id": research_job_id},
                )
            ).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rank) "
                    "VALUES('integrity-check', 1)"
                )
            )
        except Exception as exc:
            fts_available = False
            fts_error = str(exc)
    expected_vectors = {chunk.vector_id for chunk in chunks if chunk.id in active_ids}
    actual_vectors = await vectors.ids(research_job_id)
    orphan_chunks = [chunk.id for chunk in chunks if chunk.id not in active_ids]
    return {
        "research_job_id": research_job_id,
        "sql_chunks": len(chunks),
        "active_chunks": len(active_ids),
        "fts_records": fts_records,
        "vector_records": len(actual_vectors),
        "missing_vector_ids": sorted(expected_vectors - actual_vectors),
        "unexpected_vector_ids": sorted(actual_vectors - expected_vectors),
        "orphan_chunk_ids": orphan_chunks,
        "fts5_integrity_ok": fts_available
        or (fts_error is not None and "no such table" in fts_error.lower()),
        "fts5_detail": "fallback BM25"
        if fts_error and "no such table" in fts_error.lower()
        else fts_error,
        "consistent": (
            expected_vectors == actual_vectors
            and not orphan_chunks
            and (fts_records is None or fts_records == len(chunks))
            and (fts_available or (fts_error is not None and "no such table" in fts_error.lower()))
        ),
    }
