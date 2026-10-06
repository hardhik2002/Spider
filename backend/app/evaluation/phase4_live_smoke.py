"""Index a copy of the existing Phase 3 live job and inspect retrieved evidence."""

import argparse
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from app.core.config import Settings
from app.main import create_app
from app.rag.consistency import check_consistency
from app.schemas.rag import RetrievalRequest

QUERIES = [
    "What architectural differences exist between GraphRAG and traditional RAG?",
    "What evidence compares retrieval performance?",
    "What are limitations of GraphRAG?",
]


async def run(source: Path, job_id: str, output: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    with tempfile.TemporaryDirectory(prefix="spidermind-phase4-live-") as directory:
        base = Path(directory)
        database = base / "phase3-copy.db"
        shutil.copy2(source, database)
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{database}",
            qdrant_path=str(base / "qdrant"),
        )
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            index = await app.state.index_service.start(job_id)
            await asyncio.gather(*app.state.index_service.tasks)
            async with app.state.index_service.sessions() as session:
                index = await session.get(type(index), index.id)
                index_result = {
                    key: getattr(index, key) for key in (
                        "status", "documents_discovered", "documents_processed",
                        "documents_indexed", "documents_failed", "chunks_created",
                        "chunks_embedded", "vector_records", "lexical_records",
                        "embedding_dimension", "embedding_duration_ms", "duration_ms",
                        "error_message",
                    )
                }
            if index.status not in ("COMPLETED", "PARTIAL"):
                raise RuntimeError(f"Live indexing failed: {index.error_message}")
            consistency = await check_consistency(
                app.state.index_service.sessions,
                app.state.index_service.vectors,
                job_id,
            )
            retrievals = []
            for query in QUERIES:
                response = await app.state.retrieval_service.retrieve(
                    job_id, RetrievalRequest(query=query, retrieval_mode="hybrid", rerank=True),
                )
                retrievals.append({
                    "query": query,
                    "candidate_counts": {key: response[key] for key in (
                        "dense_candidates", "lexical_candidates", "fused_candidates",
                        "reranked_candidates",
                    )},
                    "timings": response["timings"],
                    "evidence": [{
                        "rank": row["rank"], "source_url": row["source_url"],
                        "source_title": row["source_title"],
                        "snippet": row["text"][:320],
                        "dense_rank": row["dense_rank"],
                        "lexical_rank": row["lexical_rank"],
                        "reranker_raw_score": row["reranker_raw_score"],
                    } for row in response["results"][:3]],
                })
    artifact = {
        "kind": "live_phase3_data_smoke", "source_database": source.name,
        "research_job_id": job_id, "index": index_result,
        "consistency": consistency, "retrievals": retrievals,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("spidermind-phase3-smoke-final.db"))
    parser.add_argument("--job-id", default="38f3b146-6170-49c2-8b5c-64677210dd3f")
    parser.add_argument("--output", type=Path, default=Path("docs/phase4-live-smoke-results.json"))
    args = parser.parse_args()
    asyncio.run(run(args.source, args.job_id, args.output))
