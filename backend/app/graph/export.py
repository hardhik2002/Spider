"""Stable, frontend-ready graph JSON export."""

import json

from app.graph.builder import digest
from app.graph.projection import GraphProjection
from app.graph.types import GRAPH_SCHEMA_VERSION


class GraphExporter:
    def export(
        self,
        graph: GraphProjection,
        *,
        snapshot_id: str,
        research_job_id: str,
        evidence_job_id: str,
    ) -> dict:
        nodes = [graph.nodes[key] for key in sorted(graph.nodes)]
        edges = [graph.edges[key] for key in sorted(graph.edges)]
        return {
            "graph": {
                "snapshot_id": snapshot_id,
                "research_job_id": research_job_id,
                "evidence_job_id": evidence_job_id,
                "schema_version": GRAPH_SCHEMA_VERSION,
                "graph_content_hash": digest(
                    {
                        "schema_version": GRAPH_SCHEMA_VERSION,
                        "nodes": nodes,
                        "edges": edges,
                    }
                ),
            },
            "nodes": nodes,
            "edges": edges,
        }

    def canonical_json(self, payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
