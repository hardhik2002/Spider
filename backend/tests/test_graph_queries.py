"""Deterministic graph traversal, analytics, and export tests."""

import json

import pytest
from app.core.config import Settings
from app.db.database import initialize_database, make_engine, make_session_factory
from app.graph.projection import GraphProjection
from app.graph.query import GraphQueryService
from app.graph.service import GraphService
from app.graph.types import EdgeType, NodeType
from tests.graph_fixture import seed_graph_fixture


@pytest.mark.asyncio
async def test_graph_queries_paths_neighborhood_analytics_and_export(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'graph.db'}")
    await initialize_database(engine)
    sessions = make_session_factory(engine)
    ids = await seed_graph_fixture(sessions)
    service = GraphService(sessions, Settings())
    query = GraphQueryService(service)
    try:
        _, snapshot = await service.start(ids["research_job_id"], ids["evidence_job_id"])
        await service.tasks.pop()
        snapshot, graph = await query.load(ids["research_job_id"], snapshot.id)
        assert query.list_nodes(graph, node_type=NodeType.CLAIM)["total"] == 6
        assert query.list_nodes(graph, claim_status="CONTESTED")["total"] == 1
        assert query.list_nodes(graph, node_type=NodeType.CLAIM, limit=2, offset=1)["total"] == 6
        assert query.list_edges(graph, edge_type=EdgeType.SUPPORTED_BY)["total"] == 5
        assert query.list_edges(
            graph, claim_id=ids["claims"]["CONTESTED"], edge_type=EdgeType.CONTRADICTED_BY
        )["total"] == 1
        contested = query.claim_view(graph, ids["claims"]["CONTESTED"])
        assert contested["status"] == "CONTESTED"
        assert len(contested["supporting_chunks"]) == 1
        assert len(contested["contradicting_chunks"]) == 1
        assert len(contested["citations"]) == 2
        assert len(contested["source_groups"]) == 2
        assert query.contradictions(graph)["claims"]
        root = f"research:{ids['research_job_id']}"
        claim = f"claim:{ids['claims']['CONTESTED']}"
        citation = contested["citations"][0]["id"]
        paths = graph.shortest_paths(root, citation, max_hops=8, max_paths=3)
        assert paths and [edge["type"] for edge in paths[0]["edges"]] == [
            EdgeType.HAS_SUBQUESTION, EdgeType.HAS_CLAIM, EdgeType.HAS_CITATION,
        ]
        assert graph.shortest_paths(claim, root, max_hops=8, max_paths=3) == []
        neighborhood = graph.neighborhood(claim, depth=1, direction="outgoing", max_nodes=3)
        assert len(neighborhood["nodes"]) == 3 and neighborhood["truncated"]
        incoming = graph.neighborhood(claim, depth=1, direction="incoming")
        assert any(node["type"] == NodeType.SUBQUESTION for node in incoming["nodes"])
        summary = query.summary(graph)
        assert summary["claims_total"] == 6
        assert summary["claims_contested"] == summary["claims_contradicted"] == 1
        assert summary["claims_without_valid_citations"] == 2
        assert summary["claims_with_multiple_independent_sources"] == 1
        assert summary["source_group_contribution"][ids["groups"][0]]["unique_documents"] == 2
        assert await query.node_detail(
            graph, f"evidence_chunk:{ids['chunks'][0]}",
            include_text=True, research_job_id=ids["research_job_id"],
        )
        payload = query.export(graph, snapshot)
        serialized = query.exporter.canonical_json(payload)
        assert json.loads(serialized) == payload
        assert serialized == query.exporter.canonical_json(query.export(graph, snapshot))
        assert payload["graph"]["graph_content_hash"] == snapshot.graph_content_hash
        assert "vector_id" not in serialized and "embedding" not in serialized
        assert all(node["id"] != "claim:" + ids["claims"].get("OTHER", "")
                   for node in payload["nodes"])
    finally:
        await service.shutdown()
        await engine.dispose()
