"""Controlled deterministic Phase 7 graph evaluation; no network or models."""

import asyncio
import json
import tempfile
from pathlib import Path

from app.core.config import Settings
from app.db.database import initialize_database, make_engine, make_session_factory
from app.evidence.models import ClaimEvidenceRelation
from app.graph.query import GraphQueryService
from app.graph.service import GraphService
from app.graph.types import EdgeType, NodeType
from sqlalchemy import select
from tests.graph_fixture import seed_graph_fixture


async def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        engine = make_engine(f"sqlite+aiosqlite:///{Path(folder) / 'phase7.db'}")
        await initialize_database(engine)
        sessions = make_session_factory(engine)
        ids = await seed_graph_fixture(sessions)
        service = GraphService(sessions, Settings())
        query = GraphQueryService(service)
        try:
            job, snapshot = await service.start(ids["research_job_id"], ids["evidence_job_id"])
            await asyncio.gather(*service.tasks)
            snapshot, graph = await query.load(ids["research_job_id"], snapshot.id)
            validation = await service.consistency(ids["research_job_id"], snapshot.id)
            first_hash = snapshot.graph_content_hash
            reused_job, reused = await service.start(ids["research_job_id"], ids["evidence_job_id"])
            export = query.export(graph, snapshot)
            roundtrip = json.loads(query.exporter.canonical_json(export)) == export
            contested = query.claim_view(graph, ids["claims"]["CONTESTED"])
            root = f"research:{ids['research_job_id']}"
            citation = contested["citations"][0]["id"]
            query_checks = {
                "contested_support_chunks": {
                    "expected": [f"evidence_chunk:{ids['chunks'][0]}"],
                    "actual": [x["id"] for x in contested["supporting_chunks"]],
                },
                "contested_contradiction_chunks": {
                    "expected": [f"evidence_chunk:{ids['chunks'][1]}"],
                    "actual": [x["id"] for x in contested["contradicting_chunks"]],
                },
                "insufficient_claims": {
                    "expected": [f"claim:{ids['claims']['INSUFFICIENT_EVIDENCE']}"],
                    "actual": [
                        x["id"]
                        for x in query.list_nodes(
                            graph,
                            node_type=NodeType.CLAIM,
                            claim_status="INSUFFICIENT_EVIDENCE",
                        )["nodes"]
                    ],
                },
                "contradiction_source_groups": {
                    "expected": [f"source_group:{ids['groups'][1]}"],
                    "actual": sorted(
                        {
                            f"source_group:{edge['properties']['source_group_id']}"
                            for edge in contested["edges"]
                            if edge["type"] == EdgeType.CONTRADICTED_BY
                        }
                    ),
                },
                "research_to_citation_path": {
                    "expected": ["HAS_SUBQUESTION", "HAS_CLAIM", "HAS_CITATION"],
                    "actual": [
                        x["type"]
                        for x in graph.shortest_paths(root, citation, max_hops=8, max_paths=1)[0][
                            "edges"
                        ]
                    ],
                },
            }
            query_accuracy = sum(
                item["expected"] == item["actual"] for item in query_checks.values()
            ) / len(query_checks)
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
            _, changed = await service.start(ids["research_job_id"], ids["evidence_job_id"])
            await asyncio.gather(*service.tasks)
            changed = await service.store.get_snapshot(changed.id)
            summary = query.summary(graph)
            result = {
                "kind": "controlled_deterministic_fixture",
                "fixture": "backend/tests/graph_fixture.py",
                "database_schema_version": 7,
                "graph_schema_version": 1,
                "snapshot_id": snapshot.id,
                "research_job_id": ids["research_job_id"],
                "evidence_job_id": ids["evidence_job_id"],
                "node_count": snapshot.node_count,
                "edge_count": snapshot.edge_count,
                "nodes_by_type": summary["nodes_by_type"],
                "edges_by_type": summary["edges_by_type"],
                "expected_node_count": 24,
                "expected_edge_count": 42,
                "coverage_metrics": {
                    key: validation[key]
                    for key in (
                        "node_coverage",
                        "edge_coverage",
                        "claim_provenance_path_coverage",
                        "citation_path_coverage",
                        "source_group_path_coverage",
                    )
                },
                "integrity_metrics": {
                    key: validation[key]
                    for key in (
                        "orphan_node_count",
                        "orphan_edge_count",
                        "cross_job_leakage_count",
                        "connected_components",
                    )
                },
                "query_checks": query_checks,
                "graph_query_exact_set_accuracy": query_accuracy,
                "determinism": {
                    "first_graph_hash": first_hash,
                    "repeated_graph_hash": reused.graph_content_hash,
                    "reused_snapshot": reused.id == snapshot.id,
                    "reused_build_job": reused_job.id == job.id,
                    "changed_data_graph_hash": changed.graph_content_hash,
                    "changed_data_new_snapshot": changed.id != snapshot.id,
                },
                "export": {
                    "format": "json",
                    "round_trip_exact": roundtrip,
                    "deterministic_ordering": (
                        [x["id"] for x in export["nodes"]]
                        == sorted(x["id"] for x in export["nodes"])
                        and [x["id"] for x in export["edges"]]
                        == sorted(x["id"] for x in export["edges"])
                    ),
                    "contains_embedding_vectors": "vector_id" in json.dumps(export),
                },
                "timings_ms": json.loads(snapshot.timings_json),
                "validation_status": validation["status"],
                "validation_issues": validation["issues"],
                "high_severity_failures": {
                    "cross_job_graph_leakage": validation["cross_job_leakage_count"],
                    "edge_with_missing_endpoint": validation["orphan_edge_count"],
                    "wrong_subquestion": sum(
                        x["code"] == "CLAIM_WRONG_SUBQUESTION" for x in validation["issues"]
                    ),
                    "invalid_citation": sum(
                        x["code"] == "INVALID_CITATION_SPAN" for x in validation["issues"]
                    ),
                    "missing_support_or_contradiction_edge": sum(
                        x["code"] == "MISSING_RELATION_EDGE" for x in validation["issues"]
                    ),
                    "claim_status_modified": sum(
                        x["code"] == "CLAIM_STATUS_MODIFIED" for x in validation["issues"]
                    ),
                    "claim_confidence_modified": sum(
                        x["code"] == "CLAIM_CONFIDENCE_MODIFIED" for x in validation["issues"]
                    ),
                    "graph_rebuild_nondeterminism": int(first_hash != reused.graph_content_hash),
                },
            }
            path = Path("docs/phase7-graph-results.json")
            path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(
                json.dumps(
                    {
                        "result": str(path),
                        "nodes": snapshot.node_count,
                        "edges": snapshot.edge_count,
                        "validation": validation["status"],
                        "query_accuracy": query_accuracy,
                    }
                )
            )
        finally:
            await service.shutdown()
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
