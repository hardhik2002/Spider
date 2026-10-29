"""Phase 7 materialization, idempotency, integrity and migration tests."""

import json
import sqlite3

import pytest
from app.core.config import Settings
from app.db.database import initialize_database, make_engine, make_session_factory
from app.evidence.models import ClaimEvidenceRelation
from app.graph.models import GraphEdge
from app.graph.projection import GraphProjection
from app.graph.service import GraphService
from app.graph.types import EdgeType, NodeType
from sqlalchemy import select
from tests.graph_fixture import seed_graph_fixture


@pytest.mark.asyncio
async def test_graph_build_all_types_mirrors_uncited_and_idempotency(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'graph.db'}")
    await initialize_database(engine)
    sessions = make_session_factory(engine)
    ids = await seed_graph_fixture(sessions)
    service = GraphService(sessions, Settings())
    try:
        job, snapshot = await service.start(ids["research_job_id"], ids["evidence_job_id"])
        await service.tasks.pop()
        saved = await service.store.get_snapshot(snapshot.id)
        assert saved.status == "COMPLETED"
        assert (saved.node_count, saved.edge_count) == (24, 42)
        assert saved.validation_status == "VALID"
        graph = GraphProjection(
            await service.store.list_nodes(saved.id), await service.store.list_edges(saved.id)
        )
        assert set(node["type"] for node in graph.nodes.values()) == set(NodeType)
        assert set(edge["type"] for edge in graph.edges.values()) == set(EdgeType)
        assert (
            len(
                [
                    edge
                    for edge in graph.edges.values()
                    if edge["type"] == EdgeType.PART_OF_DOCUMENT
                    and edge["source"] == f"evidence_chunk:{ids['chunks'][0]}"
                ]
            )
            == 2
        )
        assert (
            len(
                [
                    edge
                    for edge in graph.edges.values()
                    if edge["type"] == EdgeType.MEMBER_OF_SOURCE_GROUP
                    and edge["target"] == f"source_group:{ids['groups'][0]}"
                ]
            )
            == 2
        )
        uncited = [
            edge
            for edge in graph.edges.values()
            if edge["source"] == f"claim:{ids['claims']['INSUFFICIENT_EVIDENCE']}"
            and edge["type"] == EdgeType.SUPPORTED_BY
        ]
        assert len(uncited) == 1 and uncited[0]["properties"]["citation_valid"] is False
        assert not any(
            edge["source"] == f"claim:{ids['claims']['INSUFFICIENT_EVIDENCE']}"
            and edge["type"] == EdgeType.HAS_CITATION
            for edge in graph.edges.values()
        )
        assert not any(
            "other job secret" in json.dumps(node).lower() for node in graph.nodes.values()
        )
        repeated_job, repeated = await service.start(ids["research_job_id"], ids["evidence_job_id"])
        assert repeated.id == saved.id and repeated_job.id == job.id
        assert repeated.graph_content_hash == saved.graph_content_hash
        consistency = await service.consistency(ids["research_job_id"], saved.id)
        assert consistency["status"] == "VALID"
        assert consistency["node_coverage"] == consistency["edge_coverage"] == 1.0
        assert consistency["citation_path_coverage"] == 1.0
        assert consistency["claim_provenance_path_coverage"] == 1.0
        assert consistency["orphan_edge_count"] == 0
        assert consistency["cross_job_leakage_count"] == 0

        async with sessions() as session:
            relation = (
                await session.scalars(
                    select(ClaimEvidenceRelation).where(
                        ClaimEvidenceRelation.claim_id == ids["claims"]["SUPPORTED"]
                    )
                )
            ).one()
            relation.retrieval_score = 0.4
            await session.commit()
        changed_job, changed = await service.start(ids["research_job_id"], ids["evidence_job_id"])
        await service.tasks.pop()
        changed = await service.store.get_snapshot(changed.id)
        assert changed.id != saved.id
        assert changed.graph_content_hash != saved.graph_content_hash
        assert changed.status == "COMPLETED"
        assert changed_job.id != job.id
        stale = await service.consistency(ids["research_job_id"], saved.id)
        assert stale["status"] == "INVALID"
        assert not stale["input_fingerprint_matches"]
    finally:
        await service.shutdown()
        await engine.dispose()


@pytest.mark.asyncio
async def test_graph_validation_detects_missing_endpoint_and_uncited_filter(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'graph.db'}")
    await initialize_database(engine)
    sessions = make_session_factory(engine)
    ids = await seed_graph_fixture(sessions)
    service = GraphService(sessions, Settings())
    try:
        _, snapshot = await service.start(ids["research_job_id"], ids["evidence_job_id"], False)
        await service.tasks.pop()
        saved = await service.store.get_snapshot(snapshot.id)
        assert saved.status == "COMPLETED"
        assert saved.edge_count == 40
        async with sessions() as session:
            edge = (
                await session.scalars(
                    select(GraphEdge).where(
                        GraphEdge.snapshot_id == saved.id,
                        GraphEdge.edge_type == EdgeType.HAS_CITATION,
                    )
                )
            ).first()
            edge.target_node_id = "citation:missing"
            await session.commit()
        result = await service.consistency(ids["research_job_id"], saved.id)
        assert result["status"] == "INVALID"
        assert result["orphan_edge_count"] == 1
        assert "MISSING_EDGE_TARGET" in {issue["code"] for issue in result["issues"]}
    finally:
        await service.shutdown()
        await engine.dispose()


@pytest.mark.asyncio
async def test_graph_schema_migrates_additively_from_six(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 6")
        connection.execute("CREATE TABLE existing_marker (value TEXT)")
        connection.execute("INSERT INTO existing_marker VALUES ('preserved')")
    engine = make_engine(f"sqlite+aiosqlite:///{path}")
    try:
        await initialize_database(engine)
        await initialize_database(engine)
    finally:
        await engine.dispose()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        assert connection.execute("SELECT value FROM existing_marker").fetchone()[0] == "preserved"
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"graph_build_jobs", "graph_snapshots", "graph_nodes", "graph_edges"} <= tables
