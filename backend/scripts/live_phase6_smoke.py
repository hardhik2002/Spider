"""One bounded Phase 5 to Phase 6 connectivity smoke on copied local data."""

import asyncio
import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.core.config import Settings  # noqa: E402
from app.evidence.models import EvidenceJob  # noqa: E402
from app.main import create_app  # noqa: E402

RESEARCH_ID = "38f3b146-6170-49c2-8b5c-64677210dd3f"
AGENT_RUN_ID = "6c6a736f-5913-40ee-b144-3521cbd50fd5"


async def main() -> None:
    source_db = ROOT / "data" / "phase5-live-20261009-135809.db"
    source_vectors = ROOT / "data" / "phase5-live-qdrant-20261009-135809"
    if not source_db.exists() or not source_vectors.exists():
        raise FileNotFoundError("Prior Phase 5 smoke database and vector index are required")
    tag = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    db = ROOT / "data" / f"phase6-live-{tag}.db"
    vectors = ROOT / "data" / f"phase6-live-qdrant-{tag}"
    checkpoints = ROOT / "data" / f"phase6-live-checkpoints-{tag}.sqlite"
    shutil.copy2(source_db, db)
    shutil.copytree(source_vectors, vectors)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db.as_posix()}",
        qdrant_path=str(vectors),
        agent_checkpoint_path=str(checkpoints),
        rerank_enabled=False,
        neighbor_expansion_enabled=False,
        evidence_nli_batch_size=1,
    )
    app = create_app(settings)
    request = {
        "agent_run_id": AGENT_RUN_ID,
        "max_subquestions": 1,
        "max_claims_per_subquestion": 2,
        "evidence_candidates_per_claim": 2,
        "final_evidence_per_claim": 2,
        "max_claim_extraction_chunks": 2,
        "max_chars_per_chunk": 900,
        "max_claim_extraction_chars": 1800,
        "max_runtime_seconds": 900,
        "use_nli": True,
        "use_llm_adjudication": False,
        "use_counterqueries": False,
        "rerank": False,
    }
    report = {
        "kind": "single bounded live connectivity smoke, not a labeled accuracy benchmark",
        "research_job_id": RESEARCH_ID,
        "agent_run_id": AGENT_RUN_ID,
        "source_database": str(source_db),
        "copied_database": str(db),
        "request": request,
        "started_at_utc": datetime.now(UTC).isoformat(),
    }
    output = ROOT / "docs" / "phase6-live-smoke-results.json"
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                path = f"/api/v1/research/{RESEARCH_ID}/evidence"
                started = await api.post(path, json=request)
                started.raise_for_status()
                job_id = started.json()["evidence_job_id"]
                report["evidence_job_id"] = job_id
                print(f"Evidence job: {job_id}", flush=True)
                deadline = time.monotonic() + 1050
                while time.monotonic() < deadline:
                    async with app.state.evidence_service.sessions() as session:
                        row = await session.get(EvidenceJob, job_id)
                        if row.status in {"COMPLETED", "PARTIAL", "FAILED"}:
                            break
                    await asyncio.sleep(1)
                else:
                    report["timeout"] = True
                report["status"] = (await api.get(f"{path}/{job_id}")).json()
                report["claims"] = (await api.get(f"{path}/{job_id}/claims")).json()["claims"]
                report["contradictions"] = (
                    await api.get(f"{path}/{job_id}/contradictions")
                ).json()["claims"]
                report["ledger"] = (await api.get(f"{path}/{job_id}/ledger")).json()
                report["examples"] = [
                    (await api.get(f"{path}/{job_id}/claims/{claim['id']}")).json()
                    for claim in report["claims"][:3]
                ]
    except Exception as exc:
        report["script_error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
