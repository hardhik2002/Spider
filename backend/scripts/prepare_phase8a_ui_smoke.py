"""Prepare a local Phase 8A demo graph from a copy of completed Phase 6 data."""

import asyncio
import json
import shutil
from pathlib import Path

from app.core.config import Settings
from app.db.database import initialize_database, make_engine, make_session_factory
from app.evidence.models import EvidenceJob
from app.graph.service import GraphService
from sqlalchemy import select


async def main() -> None:
    source = Path("data/phase6-live-20261010-102304.db")
    target = Path("data/phase8a-ui-smoke.db")
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    engine = make_engine(f"sqlite+aiosqlite:///{target.resolve().as_posix()}")
    await initialize_database(engine)
    sessions = make_session_factory(engine)
    service = GraphService(sessions, Settings())
    try:
        async with sessions() as session:
            evidence = (
                await session.scalars(
                    select(EvidenceJob)
                    .where(EvidenceJob.status == "COMPLETED")
                    .order_by(EvidenceJob.completed_at.desc())
                )
            ).first()
        if evidence is None:
            raise ValueError("No completed evidence job in copied Phase 6 database")
        _, snapshot = await service.start(evidence.research_job_id, evidence.id)
        if service.tasks:
            await asyncio.gather(*service.tasks)
        snapshot = await service.store.get_snapshot(snapshot.id)
        print(
            json.dumps(
                {
                    "database": str(target),
                    "research_job_id": evidence.research_job_id,
                    "evidence_job_id": evidence.id,
                    "graph_snapshot_id": snapshot.id,
                    "graph_status": snapshot.status,
                    "node_count": snapshot.node_count,
                    "edge_count": snapshot.edge_count,
                }
            )
        )
    finally:
        await service.shutdown()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
