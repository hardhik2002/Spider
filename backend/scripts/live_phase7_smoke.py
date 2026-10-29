"""Offline graph build on a copy of the successful Phase 6 live evidence database."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from app.core.config import Settings
from app.db.database import initialize_database, make_engine, make_session_factory
from app.evidence.models import EvidenceJob
from app.graph.query import GraphQueryService
from app.graph.service import GraphService
from sqlalchemy import select


async def main() -> None:
    source = Path("data/phase6-live-20261010-102304.db")
    if not source.is_file():
        raise FileNotFoundError(source)
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / "phase6-live-copy.db"
        shutil.copy2(source, copy)
        engine = make_engine(f"sqlite+aiosqlite:///{copy}")
        await initialize_database(engine)
        sessions = make_session_factory(engine)
        service = GraphService(sessions, Settings())
        query = GraphQueryService(service)
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
                raise ValueError("No completed Phase 6 evidence job in live snapshot")
            _, snapshot = await service.start(evidence.research_job_id, evidence.id)
            await asyncio.gather(*service.tasks)
            snapshot, graph = await query.load(evidence.research_job_id, snapshot.id)
            validation = await service.consistency(evidence.research_job_id, snapshot.id)
            summary = query.summary(graph)
            claims = [node for node in graph.nodes.values() if node["type"] == "CLAIM"]
            examples = []
            for claim in claims[:3]:
                claim_view = query.claim_view(graph, claim["properties"]["claim_id"])
                citation_nodes = claim_view["citations"]
                research_path = graph.shortest_paths(
                    f"research:{evidence.research_job_id}",
                    claim["id"],
                    max_hops=8,
                    max_paths=1,
                )
                citation_path = (
                    graph.shortest_paths(
                        claim["id"], citation_nodes[0]["id"], max_hops=8, max_paths=1
                    )
                    if citation_nodes
                    else []
                )
                group_nodes = claim_view["source_groups"]
                source_path = (
                    graph.shortest_paths(claim["id"], group_nodes[0]["id"], max_hops=8, max_paths=1)
                    if group_nodes
                    else []
                )
                citation_trace = []
                if citation_nodes:
                    citation = citation_nodes[0]
                    chunk_id = f"evidence_chunk:{citation['properties']['chunk_id']}"
                    document_id = f"document:{citation['properties']['document_id']}"
                    groups = sorted(
                        edge["target"]
                        for edge in graph.outgoing[document_id]
                        if edge["type"] == "MEMBER_OF_SOURCE_GROUP"
                    )
                    if (
                        groups
                        and any(
                            edge["type"] == "CITES_CHUNK" and edge["target"] == chunk_id
                            for edge in graph.outgoing[citation["id"]]
                        )
                        and any(
                            edge["type"] == "PART_OF_DOCUMENT" and edge["target"] == document_id
                            for edge in graph.outgoing[chunk_id]
                        )
                    ):
                        citation_trace = [
                            claim["id"],
                            citation["id"],
                            chunk_id,
                            document_id,
                            groups[0],
                        ]
                examples.append(
                    {
                        "claim_id": claim["id"],
                        "status": claim["properties"]["status"],
                        "research_to_claim_node_ids": [x["id"] for x in research_path[0]["nodes"]]
                        if research_path
                        else [],
                        "claim_to_citation_node_ids": [x["id"] for x in citation_path[0]["nodes"]]
                        if citation_path
                        else [],
                        "claim_to_source_group_node_ids": [x["id"] for x in source_path[0]["nodes"]]
                        if source_path
                        else [],
                        "citation_ids": [x["id"] for x in citation_nodes],
                        "claim_citation_chunk_document_group_node_ids": citation_trace,
                    }
                )
            result = {
                "kind": "offline_live_phase6_to_phase7_smoke_not_labeled_accuracy_benchmark",
                "source_database": str(source),
                "research_job_id": evidence.research_job_id,
                "evidence_job_id": evidence.id,
                "graph_snapshot_id": snapshot.id,
                "graph_content_hash": snapshot.graph_content_hash,
                "status": snapshot.status,
                "validation_status": validation["status"],
                "validation_issues": validation["issues"],
                "node_count": snapshot.node_count,
                "edge_count": snapshot.edge_count,
                "nodes_by_type": summary["nodes_by_type"],
                "edges_by_type": summary["edges_by_type"],
                "claims": summary["claims_total"],
                "citations": summary["citations_total"],
                "documents": summary["documents_total"],
                "source_groups": summary["source_groups_total"],
                "support_edges": summary["support_edges"],
                "contradiction_edges": summary["contradiction_edges"],
                "path_examples": examples,
                "integrity_metrics": {
                    key: validation[key]
                    for key in (
                        "node_coverage",
                        "edge_coverage",
                        "claim_provenance_path_coverage",
                        "citation_path_coverage",
                        "source_group_path_coverage",
                        "orphan_node_count",
                        "orphan_edge_count",
                        "cross_job_leakage_count",
                    )
                },
                "timings_ms": json.loads(snapshot.timings_json),
            }
            path = Path("docs/phase7-live-smoke-results.json")
            path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(
                json.dumps(
                    {
                        "result": str(path),
                        "nodes": snapshot.node_count,
                        "edges": snapshot.edge_count,
                        "validation": validation["status"],
                    }
                )
            )
        finally:
            await service.shutdown()
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
