"""Structural graph analytics; no credibility or truth scoring."""

from collections import Counter, defaultdict

from app.graph.projection import GraphProjection
from app.graph.types import EdgeType, NodeType


class GraphAnalytics:
    def summary(self, graph: GraphProjection) -> dict:
        node_counts = Counter(node["type"] for node in graph.nodes.values())
        edge_counts = Counter(edge["type"] for edge in graph.edges.values())
        claims = [node for node in graph.nodes.values() if node["type"] == NodeType.CLAIM]
        statuses = Counter(node["properties"]["status"] for node in claims)
        source_groups: dict[str, dict[str, set]] = defaultdict(
            lambda: {
                "supported": set(),
                "contradicted": set(),
                "citations": set(),
                "documents": set(),
            }
        )
        for edge in graph.edges.values():
            kind = edge["type"]
            props = edge["properties"]
            group = props.get("source_group_id")
            if group and kind == EdgeType.SUPPORTED_BY:
                source_groups[group]["supported"].add(edge["source"])
            if group and kind == EdgeType.CONTRADICTED_BY:
                source_groups[group]["contradicted"].add(edge["source"])
            if kind == EdgeType.MEMBER_OF_SOURCE_GROUP:
                source_groups[edge["target"].split(":", 1)[1]]["documents"].add(edge["source"])
        relations = {
            edge["properties"].get("claim_evidence_relation_id"): edge
            for edge in graph.edges.values()
            if edge["type"] in {EdgeType.SUPPORTED_BY, EdgeType.CONTRADICTED_BY}
        }
        for node in graph.nodes.values():
            if node["type"] != NodeType.CITATION:
                continue
            relation = relations.get(node["properties"].get("relation_id"))
            if relation and node["properties"].get("valid"):
                group = relation["properties"].get("source_group_id")
                if group:
                    source_groups[group]["citations"].add(node["id"])
        source_contribution = {
            key: {
                "claims_supported": len(value["supported"]),
                "claims_contradicted": len(value["contradicted"]),
                "citations_provided": len(value["citations"]),
                "unique_documents": len(value["documents"]),
            }
            for key, value in sorted(source_groups.items())
        }
        support_degree = Counter(
            sum(edge["type"] == EdgeType.SUPPORTED_BY for edge in graph.outgoing[claim["id"]])
            for claim in claims
        )
        contradiction_degree = Counter(
            sum(edge["type"] == EdgeType.CONTRADICTED_BY for edge in graph.outgoing[claim["id"]])
            for claim in claims
        )
        no_citation = [
            claim["id"]
            for claim in claims
            if not any(
                edge["type"] == EdgeType.HAS_CITATION
                and graph.nodes.get(edge["target"], {}).get("properties", {}).get("valid")
                for edge in graph.outgoing[claim["id"]]
            )
        ]
        multi_source = [
            claim["id"]
            for claim in claims
            if len(
                {
                    edge["properties"].get("source_group_id")
                    for edge in graph.outgoing[claim["id"]]
                    if edge["type"] == EdgeType.SUPPORTED_BY
                    and edge["properties"].get("citation_valid")
                }
            )
            >= 2
        ]
        return {
            "claims_total": len(claims),
            "claims_corroborated": statuses["CORROBORATED"],
            "claims_supported": statuses["SUPPORTED"],
            "claims_partial": statuses["PARTIALLY_SUPPORTED"],
            "claims_contested": statuses["CONTESTED"],
            "claims_contradicted": statuses["CONTRADICTED"],
            "claims_insufficient": statuses["INSUFFICIENT_EVIDENCE"],
            "citations_total": node_counts[NodeType.CITATION],
            "documents_total": node_counts[NodeType.DOCUMENT],
            "source_groups_total": node_counts[NodeType.SOURCE_GROUP],
            "support_edges": edge_counts[EdgeType.SUPPORTED_BY],
            "partial_support_edges": edge_counts[EdgeType.PARTIALLY_SUPPORTED_BY],
            "contradiction_edges": edge_counts[EdgeType.CONTRADICTED_BY],
            "claims_without_valid_citations": len(no_citation),
            "claim_ids_without_valid_citations": sorted(no_citation),
            "claims_with_multiple_independent_sources": len(multi_source),
            "claim_ids_with_multiple_independent_sources": sorted(multi_source),
            "nodes_by_type": dict(sorted(node_counts.items())),
            "edges_by_type": dict(sorted(edge_counts.items())),
            "connected_components": len(graph.connected_components()),
            "claim_support_degree_distribution": dict(sorted(support_degree.items())),
            "claim_contradiction_degree_distribution": dict(sorted(contradiction_degree.items())),
            "source_group_contribution": source_contribution,
        }

    def claim_connectivity(self, graph: GraphProjection, claim_id: str) -> dict:
        edges = graph.outgoing[claim_id]
        chunks = {edge["target"] for edge in edges if edge["target"].startswith("evidence_chunk:")}
        documents = {
            edge["target"]
            for chunk in chunks
            for edge in graph.outgoing[chunk]
            if edge["type"] == EdgeType.PART_OF_DOCUMENT
        }
        groups = {
            edge["properties"].get("source_group_id")
            for edge in edges
            if edge["type"]
            in {EdgeType.SUPPORTED_BY, EdgeType.PARTIALLY_SUPPORTED_BY, EdgeType.CONTRADICTED_BY}
            and edge["properties"].get("source_group_id")
        }
        return {
            "origin_count": sum(e["type"] == EdgeType.ORIGINATED_FROM for e in edges),
            "support_edge_count": sum(e["type"] == EdgeType.SUPPORTED_BY for e in edges),
            "partial_support_edge_count": sum(
                e["type"] == EdgeType.PARTIALLY_SUPPORTED_BY for e in edges
            ),
            "contradiction_edge_count": sum(e["type"] == EdgeType.CONTRADICTED_BY for e in edges),
            "citation_count": sum(e["type"] == EdgeType.HAS_CITATION for e in edges),
            "document_count": len(documents),
            "independent_source_group_count": len(groups),
        }
