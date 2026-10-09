"""One bounded live Phase 5 run on a copy of existing Phase 3 research data."""

import asyncio
import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import func, select

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.agent.models import AgentRun, EvidenceAssessment  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.db.models import CrawledPage, ResearchJob  # noqa: E402
from app.main import create_app  # noqa: E402
from app.rag.models import IndexJob, KnowledgeChunk, KnowledgeDocument  # noqa: E402

RESEARCH_ID = "38f3b146-6170-49c2-8b5c-64677210dd3f"
TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "BUDGET_EXHAUSTED"}


async def wait_index(sessions, index_id, deadline):
    while time.monotonic() < deadline:
        async with sessions() as session:
            row = await session.get(IndexJob, index_id)
            if row.status in {"COMPLETED", "PARTIAL", "FAILED"}:
                return row
        await asyncio.sleep(0.5)
    raise TimeoutError("Initial indexing exceeded the smoke-test deadline")


async def wait_agent(sessions, run_id, deadline):
    while time.monotonic() < deadline:
        async with sessions() as session:
            row = await session.get(AgentRun, run_id)
            if row.status in TERMINAL:
                return row
        await asyncio.sleep(0.5)
    raise TimeoutError("Agent exceeded the smoke-test deadline")


async def counts(sessions):
    async with sessions() as session:
        documents = (
            await session.scalar(
                select(func.count())
                .select_from(KnowledgeDocument)
                .where(
                    KnowledgeDocument.research_job_id == RESEARCH_ID,
                    KnowledgeDocument.index_status == "COMPLETED",
                )
            )
        ) or 0
        chunks = (
            await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.research_job_id == RESEARCH_ID)
            )
        ) or 0
        return {"sources": documents, "chunks": chunks}


async def main():
    source = ROOT / "spidermind-phase3-smoke-final.db"
    if not source.exists():
        raise FileNotFoundError(source)
    tag = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    database = data / f"phase5-live-{tag}.db"
    shutil.copy2(source, database)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
        qdrant_path=str(data / f"phase5-live-qdrant-{tag}"),
        agent_checkpoint_path=str(data / f"phase5-live-checkpoints-{tag}.sqlite"),
        rerank_enabled=False,
        neighbor_expansion_enabled=False,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        sessions = app.state.agent_service.sessions
        async with sessions() as session:
            job = await session.get(ResearchJob, RESEARCH_ID)
            if job is None:
                raise RuntimeError("Expected Phase 3 research job missing")
        print("Indexing existing live page...", flush=True)
        initial = await app.state.index_service.start(RESEARCH_ID)
        initial = await wait_index(sessions, initial.id, time.monotonic() + 300)
        if initial.status != "COMPLETED":
            raise RuntimeError(f"Initial index failed: {initial.error_message}")
        before = await counts(sessions)
        print(f"Initial evidence: {before}", flush=True)
        request = {
            "max_iterations": 1,
            "max_new_search_queries": 1,
            "max_new_seeds": 1,
            "max_new_pages": 1,
            "max_runtime_seconds": 360,
            "min_evidence_chunks_per_subquestion": 3,
            "min_unique_sources_per_subquestion": 2,
            "retrieval_top_k": 4,
            "rerank": False,
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as api:
            response = await api.post(f"/api/v1/research/{RESEARCH_ID}/agent", json=request)
            response.raise_for_status()
            run_id = response.json()["agent_run_id"]
            print(f"Agent run: {run_id}", flush=True)
            try:
                await wait_agent(sessions, run_id, time.monotonic() + 390)
            except TimeoutError:
                await api.post(f"/api/v1/research/{RESEARCH_ID}/agent/{run_id}/cancel")
                await wait_agent(sessions, run_id, time.monotonic() + 30)
            status = (await api.get(f"/api/v1/research/{RESEARCH_ID}/agent/{run_id}")).json()
            iterations = (
                await api.get(f"/api/v1/research/{RESEARCH_ID}/agent/{run_id}/iterations")
            ).json()["iterations"]
            gaps = (await api.get(f"/api/v1/research/{RESEARCH_ID}/agent/{run_id}/gaps")).json()[
                "gaps"
            ]
        after = await counts(sessions)
        async with sessions() as session:
            assessments = (
                (
                    await session.execute(
                        select(EvidenceAssessment)
                        .where(EvidenceAssessment.agent_run_id == run_id)
                        .order_by(
                            EvidenceAssessment.iteration_number, EvidenceAssessment.subquestion_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            crawl_ids = {
                action["data"]["crawl_job_id"]
                for gap in gaps
                for action in gap["actions"]
                if action["kind"] == "CRAWL" and "crawl_job_id" in action["data"]
            }
            crawl_pages = (
                (
                    await session.execute(
                        select(CrawledPage).where(CrawledPage.crawl_job_id.in_(crawl_ids))
                    )
                )
                .scalars()
                .all()
                if crawl_ids
                else []
            )
        report = {
            "kind": "bounded_live_smoke_not_labeled_benchmark",
            "source_database": source.name,
            "copied_database": str(database),
            "research_job_id": RESEARCH_ID,
            "agent_run_id": run_id,
            "model": settings.ollama_model,
            "initial_index": {
                "status": initial.status,
                "documents_indexed": initial.documents_indexed,
                "chunks_created": initial.chunks_created,
            },
            "request": request,
            "initial_evidence": before,
            "final_evidence": after,
            "status": status,
            "initial_gaps": [g for g in gaps if g["iteration_created"] == 0],
            "gaps": gaps,
            "iterations": iterations,
            "assessments": [
                {
                    "iteration": a.iteration_number,
                    "subquestion_id": a.subquestion_id,
                    "coverage": a.coverage,
                    "deterministic_minimum_met": a.deterministic_minimum_met,
                    "needs_more_research": a.needs_more_research,
                    "chunk_ids": json.loads(a.evidence_chunk_ids_json),
                    "unique_document_count": a.unique_document_count,
                }
                for a in assessments
            ],
            "generated_searches": status["new_searches_executed"],
            "new_sources": after["sources"] - before["sources"],
            "new_evidence_chunks": after["chunks"] - before["chunks"],
            "crawl_page_outcomes": [
                {"url": p.normalized_url, "status": p.crawl_status, "error": p.error_message}
                for p in crawl_pages
            ],
            "remaining_gaps": sum(g["status"] != "RESOLVED" for g in gaps),
            "stop_reason": status["stop_reason"],
        }
        output = ROOT / "docs" / "phase5-live-smoke-results.json"
        output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {output}", flush=True)
        print(
            json.dumps(
                {
                    "status": status["status"],
                    "stop_reason": status["stop_reason"],
                    "initial": before,
                    "final": after,
                    "gaps": len(gaps),
                    "searches": status["new_searches_executed"],
                },
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
