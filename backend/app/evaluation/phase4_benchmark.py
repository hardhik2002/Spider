"""Run a controlled, labeled Phase 4 benchmark with local models."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from sqlalchemy import select

from app.core.config import Settings
from app.crawler.embedding import SentenceTransformerProvider
from app.db.models import (
    CrawledPage,
    CrawlJob,
    ResearchJob,
    ResearchSearchResult,
    ResearchSeed,
    ResearchSubquestion,
)
from app.evaluation.phase4 import aggregate, ranking_metrics
from app.main import create_app
from app.rag.models import KnowledgeDocument
from app.rag.reranker import CrossEncoderReranker
from app.schemas.rag import RetrievalRequest

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "phase4_benchmark.json"


async def run_profile(
    base: Path, profile: tuple[int, int, int], fixture: dict, embedder, reranker
) -> dict:
    target, maximum, overlap = profile
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{base / 'benchmark.db'}",
        qdrant_path=str(base / "qdrant"),
        chunk_target_tokens=target,
        chunk_max_tokens=maximum,
        chunk_overlap_tokens=overlap,
        chunk_min_tokens=20,
        final_top_k=5,
        max_final_chunks_per_document=1,
        neighbor_expansion_enabled=False,
    )
    app = create_app(settings, embedding_provider=embedder, reranker=reranker)
    async with app.router.lifespan_context(app):
        async with app.state.index_service.sessions() as session:
            research = ResearchJob(
                question="Controlled RAG retrieval benchmark",
                request_json="{}",
                planner_model="fixture",
                max_subquestions=1,
                max_total_pages=20,
            )
            session.add(research)
            await session.flush()
            sub = ResearchSubquestion(
                research_job_id=research.id,
                plan_id="benchmark",
                question=research.question,
                rationale="Controlled evaluation",
                priority="high",
                expected_evidence="[]",
                preferred_source_types="[]",
                order=0,
            )
            session.add(sub)
            await session.flush()
            for doc in fixture["documents"]:
                url = f"https://benchmark.example/{doc['key']}"
                crawl = CrawlJob(
                    start_url=url,
                    max_depth=0,
                    max_pages=1,
                    timeout=5,
                    max_content_size=100000,
                )
                result = ResearchSearchResult(
                    research_job_id=research.id,
                    normalized_url=url,
                    url=url,
                    title=doc["title"],
                    snippet="fixture",
                    domain="benchmark.example",
                    provider="fixture",
                    best_rank=1,
                )
                session.add_all([crawl, result])
                await session.flush()
                session.add_all(
                    [
                        CrawledPage(
                            crawl_job_id=crawl.id,
                            url=url,
                            normalized_url=url,
                            depth=0,
                            title=doc["title"],
                            text_content=doc["text"],
                            crawl_status="COMPLETED",
                        ),
                        ResearchSeed(
                            research_job_id=research.id,
                            subquestion_id=sub.id,
                            result_id=result.id,
                            semantic_relevance=1.0,
                            seed_score=1.0,
                            selected=True,
                            crawl_job_id=crawl.id,
                        ),
                    ]
                )
            await session.commit()
            job_id = research.id
        index = await app.state.index_service.start(job_id)
        await asyncio.gather(*app.state.index_service.tasks)
        async with app.state.index_service.sessions() as session:
            index = await session.get(type(index), index.id)
            documents = (
                (
                    await session.execute(
                        select(KnowledgeDocument).where(KnowledgeDocument.research_job_id == job_id)
                    )
                )
                .scalars()
                .all()
            )
            by_id = {d.id: d.source_url.rsplit("/", 1)[-1] for d in documents}
            index_metrics = {
                key: getattr(index, key)
                for key in (
                    "status",
                    "documents_processed",
                    "documents_failed",
                    "chunks_created",
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
                )
            }
        if index.status != "COMPLETED":
            raise RuntimeError(f"Benchmark indexing failed: {index.error_message}")
        strategies = {
            "dense": ("dense", False),
            "bm25": ("lexical", False),
            "hybrid": ("hybrid", False),
            "hybrid_rerank": ("hybrid", True),
        }
        measurements = {key: [] for key in strategies}
        latency = {key: [] for key in strategies}
        queries = []
        for item in fixture["queries"]:
            query_result = {"query": item["query"], "relevant": item["relevant"]}
            for name, (mode, rerank) in strategies.items():
                response = await app.state.retrieval_service.retrieve(
                    job_id,
                    RetrievalRequest(
                        query=item["query"],
                        retrieval_mode=mode,
                        rerank=rerank,
                        final_top_k=5,
                        include_neighbor_context=False,
                    ),
                )
                ranked = list(
                    dict.fromkeys(by_id[result["document_id"]] for result in response["results"])
                )
                metrics = ranking_metrics(ranked, item["relevant"], k=5)
                measurements[name].append(metrics)
                latency[name].append(response["timings"]["total_ms"])
                query_result[name] = {
                    "ranked_documents": ranked,
                    "metrics": metrics,
                    "timings": response["timings"],
                    "first_relevant_rank": next(
                        (i for i, doc in enumerate(ranked, 1) if doc in item["relevant"]), None
                    ),
                }
            queries.append(query_result)
        return {
            "profile": {"target_tokens": target, "max_tokens": maximum, "overlap_tokens": overlap},
            "index": index_metrics,
            "strategies": {
                name: {
                    "metrics": aggregate(measurements[name]),
                    "mean_latency_ms": sum(latency[name]) / len(latency[name]),
                }
                for name in strategies
            },
            "queries": queries,
        }


async def main(output: Path) -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    embedder = SentenceTransformerProvider("BAAI/bge-m3", batch_size=16)
    reranker = CrossEncoderReranker("BAAI/bge-reranker-v2-m3", batch_size=4)
    profiles = [(80, 110, 12), (120, 160, 18), (180, 230, 25), (500, 650, 80)]
    results = []
    with tempfile.TemporaryDirectory(prefix="spidermind-phase4-") as directory:
        for profile in profiles:
            base = Path(directory) / str(profile[0])
            base.mkdir()
            print(f"Benchmark profile {profile}", flush=True)
            results.append(await run_profile(base, profile, fixture, embedder, reranker))
    artifact = {
        "fixture": str(FIXTURE.name),
        "models": {
            "embedding": embedder.model_name,
            "reranker": reranker.model_name,
        },
        "profiles": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "summary": [
                    {"profile": row["profile"], "strategies": row["strategies"]} for row in results
                ],
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/phase4-benchmark-results.json"))
    asyncio.run(main(parser.parse_args().output))
