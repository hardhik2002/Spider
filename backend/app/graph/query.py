"""Snapshot-scoped filtering, traversal, convenience views, and export."""

from collections import Counter

from sqlalchemy import select

from app.graph.analytics import GraphAnalytics
from app.graph.builder import digest
from app.graph.export import GraphExporter
from app.graph.projection import GraphProjection
from app.graph.types import GRAPH_SCHEMA_VERSION, EdgeType, NodeType
from app.rag.models import KnowledgeChunk


class GraphQueryService:
    def __init__(self, graph_service) -> None:
        self.graph_service = graph_service
        self.analytics = GraphAnalytics()
        self.exporter = GraphExporter()

    async def load(self, research_job_id: str, snapshot_id: str):
        snapshot = await self.graph_service.snapshot(research_job_id, snapshot_id)
        if snapshot.status != "COMPLETED":
            raise ValueError("Graph snapshot is not ready")
        graph = GraphProjection(
            await self.graph_service.store.list_nodes(snapshot_id),
            await self.graph_service.store.list_edges(snapshot_id),
        )
        current_hash = digest(
            {
                "schema_version": GRAPH_SCHEMA_VERSION,
                "nodes": [graph.nodes[key] for key in sorted(graph.nodes)],
                "edges": [graph.edges[key] for key in sorted(graph.edges)],
            }
        )
        if current_hash != snapshot.graph_content_hash:
            raise ValueError("Graph snapshot integrity check failed")
        return snapshot, graph

    @staticmethod
    def compact(node: dict) -> dict:
        result = {**node, "properties": dict(node["properties"])}
        if node["type"] == NodeType.CITATION:
            result["properties"].pop("exact_text", None)
        return result

    def list_nodes(
        self,
        graph: GraphProjection,
        *,
        node_type: str | None = None,
        subquestion_id: str | None = None,
        claim_status: str | None = None,
        confidence: str | None = None,
        source_domain: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        nodes = []
        for key in sorted(graph.nodes):
            node = graph.nodes[key]
            props = node["properties"]
            if node_type and node["type"] != node_type:
                continue
            if (
                subquestion_id
                and str(props.get("subquestion_id", props.get("plan_id"))) != subquestion_id
            ):
                continue
            if claim_status and props.get("status") != claim_status:
                continue
            if confidence and props.get("confidence_tier") != confidence:
                continue
            if source_domain and props.get("source_domain") != source_domain.lower():
                continue
            nodes.append(self.compact(node))
        return {"total": len(nodes), "nodes": nodes[offset : offset + limit]}

    def list_edges(
        self,
        graph: GraphProjection,
        *,
        edge_type: str | None = None,
        source_node_id: str | None = None,
        target_node_id: str | None = None,
        claim_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        edges = [
            edge
            for key, edge in sorted(graph.edges.items())
            if (not edge_type or edge["type"] == edge_type)
            and (not source_node_id or edge["source"] == source_node_id)
            and (not target_node_id or edge["target"] == target_node_id)
            and (
                not claim_id
                or edge["source"] == f"claim:{claim_id}"
                or edge["target"] == f"claim:{claim_id}"
            )
        ]
        return {"total": len(edges), "edges": edges[offset : offset + limit]}

    async def node_detail(
        self,
        graph: GraphProjection,
        node_id: str,
        *,
        include_text: bool = False,
        research_job_id: str,
    ) -> dict:
        if node_id not in graph.nodes:
            raise KeyError(node_id)
        node = {**graph.nodes[node_id], "properties": dict(graph.nodes[node_id]["properties"])}
        if include_text and node["type"] == NodeType.EVIDENCE_CHUNK:
            async with self.graph_service.sessions() as session:
                chunk = await session.get(KnowledgeChunk, node["properties"]["chunk_id"])
                if chunk is not None and chunk.research_job_id == research_job_id:
                    node["text"] = chunk.text
        incoming = Counter(edge["type"] for edge in graph.incoming[node_id])
        outgoing = Counter(edge["type"] for edge in graph.outgoing[node_id])
        neighbor_ids = {edge["source"] for edge in graph.incoming[node_id]} | {
            edge["target"] for edge in graph.outgoing[node_id]
        }
        return {
            "node": node,
            "incoming_edge_counts": dict(sorted(incoming.items())),
            "outgoing_edge_counts": dict(sorted(outgoing.items())),
            "neighbors": [
                self.compact(graph.nodes[key]) for key in sorted(neighbor_ids) if key in graph.nodes
            ],
        }

    def claim_view(self, graph: GraphProjection, claim_id: str) -> dict:
        root = f"claim:{claim_id}"
        if root not in graph.nodes:
            raise KeyError(claim_id)
        ids = {root}
        edges = {}
        for edge in graph.incident(root):
            ids.update((edge["source"], edge["target"]))
            edges[edge["id"]] = edge
        for citation in [key for key in ids if key.startswith("citation:")]:
            for edge in graph.outgoing[citation]:
                if edge["type"] == EdgeType.CITES_CHUNK:
                    ids.add(edge["target"])
                    edges[edge["id"]] = edge
        for chunk in [key for key in ids if key.startswith("evidence_chunk:")]:
            for edge in graph.outgoing[chunk]:
                if edge["type"] == EdgeType.PART_OF_DOCUMENT:
                    ids.add(edge["target"])
                    edges[edge["id"]] = edge
        for document in [key for key in ids if key.startswith("document:")]:
            for edge in graph.outgoing[document]:
                if edge["type"] == EdgeType.MEMBER_OF_SOURCE_GROUP:
                    ids.add(edge["target"])
                    edges[edge["id"]] = edge
        return {
            "claim": graph.nodes[root],
            "subquestions": [
                graph.nodes[key] for key in sorted(ids) if key.startswith("subquestion:")
            ],
            "origin_chunks": self._targets(edges, graph, EdgeType.ORIGINATED_FROM),
            "supporting_chunks": self._targets(edges, graph, EdgeType.SUPPORTED_BY),
            "partial_support_chunks": self._targets(edges, graph, EdgeType.PARTIALLY_SUPPORTED_BY),
            "contradicting_chunks": self._targets(edges, graph, EdgeType.CONTRADICTED_BY),
            "citations": [graph.nodes[key] for key in sorted(ids) if key.startswith("citation:")],
            "documents": [graph.nodes[key] for key in sorted(ids) if key.startswith("document:")],
            "source_groups": [
                graph.nodes[key] for key in sorted(ids) if key.startswith("source_group:")
            ],
            "status": graph.nodes[root]["properties"]["status"],
            "confidence": graph.nodes[root]["properties"]["confidence_tier"],
            "connectivity": self.analytics.claim_connectivity(graph, root),
            "nodes": [graph.nodes[key] for key in sorted(ids) if key in graph.nodes],
            "edges": [edges[key] for key in sorted(edges)],
        }

    @staticmethod
    def _targets(edges: dict, graph: GraphProjection, edge_type: EdgeType) -> list[dict]:
        ids = {edge["target"] for edge in edges.values() if edge["type"] == edge_type}
        return [graph.nodes[key] for key in sorted(ids) if key in graph.nodes]

    def contradictions(self, graph: GraphProjection) -> dict:
        ids = [
            node["properties"]["claim_id"]
            for node in graph.nodes.values()
            if node["type"] == NodeType.CLAIM
            and (
                node["properties"]["status"] in {"CONTESTED", "CONTRADICTED"}
                or any(
                    edge["type"] == EdgeType.CONTRADICTED_BY for edge in graph.outgoing[node["id"]]
                )
            )
        ]
        return {"claims": [self.claim_view(graph, claim_id) for claim_id in sorted(ids)]}

    def summary(self, graph: GraphProjection) -> dict:
        return self.analytics.summary(graph)

    def export(self, graph: GraphProjection, snapshot) -> dict:
        return self.exporter.export(
            graph,
            snapshot_id=snapshot.id,
            research_job_id=snapshot.research_job_id,
            evidence_job_id=snapshot.evidence_job_id,
        )

    async def export_with_optional_text(self, graph: GraphProjection, snapshot) -> dict:
        payload = self.export(graph, snapshot)
        if not self.graph_service.settings.graph_export_include_chunk_text:
            return payload
        payload["nodes"] = [
            {**node, "properties": dict(node["properties"])} for node in payload["nodes"]
        ]
        chunk_ids = [
            node["properties"]["chunk_id"]
            for node in payload["nodes"]
            if node["type"] == NodeType.EVIDENCE_CHUNK
        ]
        if not chunk_ids:
            return payload
        async with self.graph_service.sessions() as session:
            rows = (
                await session.scalars(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.research_job_id == snapshot.research_job_id,
                        KnowledgeChunk.id.in_(chunk_ids),
                    )
                )
            ).all()
        text_by_id = {row.id: row.text[:1200] for row in rows}
        for node in payload["nodes"]:
            if node["type"] == NodeType.EVIDENCE_CHUNK:
                node["properties"]["text_excerpt"] = text_by_id.get(node["properties"]["chunk_id"])
        return payload
