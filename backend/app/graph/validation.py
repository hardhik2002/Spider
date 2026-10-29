"""Graph integrity and consistency against the authoritative domain records."""

import json
from collections import Counter

from app.evidence.logic import validate_citation
from app.graph.builder import GraphMaterial, SourceState, digest, node_id
from app.graph.projection import GraphProjection
from app.graph.types import EdgeType, NodeType


class GraphValidator:
    def validate(
        self, state: SourceState, expected: GraphMaterial, actual: GraphProjection
    ) -> dict:
        issues: list[dict] = []

        def issue(code: str, **details) -> None:
            issues.append({"code": code, **details})

        expected_nodes, actual_nodes = set(expected.nodes), set(actual.nodes)
        expected_edges, actual_edges = set(expected.edges), set(actual.edges)
        for key in sorted(expected_nodes - actual_nodes):
            issue("MISSING_NODE", node_id=key)
        for key in sorted(actual_nodes - expected_nodes):
            issue("UNEXPECTED_NODE", node_id=key)
        for key in sorted(expected_edges - actual_edges):
            issue("MISSING_EDGE", edge_id=key)
        for key in sorted(actual_edges - expected_edges):
            issue("UNEXPECTED_EDGE", edge_id=key)
        for key in sorted(expected_nodes & actual_nodes):
            if expected.nodes[key] != actual.nodes[key]:
                issue("NODE_PROPERTY_MISMATCH", node_id=key)
        for key in sorted(expected_edges & actual_edges):
            if expected.edges[key] != actual.edges[key]:
                issue("EDGE_PROPERTY_MISMATCH", edge_id=key)

        orphan_edges = []
        for edge in actual.edges.values():
            if edge["source"] not in actual.nodes:
                orphan_edges.append(edge["id"])
                issue("MISSING_EDGE_SOURCE", edge_id=edge["id"], source_node_id=edge["source"])
            if edge["target"] not in actual.nodes:
                orphan_edges.append(edge["id"])
                issue("MISSING_EDGE_TARGET", edge_id=edge["id"], target_node_id=edge["target"])

        subquestions = {row.id: row for row in state.subquestions}
        claims = {row.id: row for row in state.claims}
        chunks = {row.id: row for row in state.chunks}
        documents = {row.id: row for row in state.documents}
        relations = {row.id: row for row in state.relations}
        groups = {row.id: row for row in state.groups}
        source_pairs = {(row.chunk_id, row.document_id) for row in state.sources}
        cited_relation_ids = set()
        valid_citation_count = 0
        citation_path_count = 0
        source_group_path_count = 0
        for row in state.citations:
            relation = relations.get(row.relation_id)
            chunk = chunks.get(row.chunk_id)
            valid = bool(
                relation
                and chunk
                and relation.claim_id == row.claim_id
                and relation.chunk_id == row.chunk_id
                and relation.document_id == row.document_id
                and relation.source_url == row.source_url
                and validate_citation(chunk.text, row.start_offset, row.end_offset, row.exact_text)
            )
            if not valid:
                issue("INVALID_CITATION_SPAN", citation_id=row.id)
                continue
            cited_relation_ids.add(row.relation_id)
            valid_citation_count += 1
            claim_key = node_id(NodeType.CLAIM, row.claim_id)
            citation_key = node_id(NodeType.CITATION, row.id)
            chunk_key = node_id(NodeType.EVIDENCE_CHUNK, row.chunk_id)
            if self._edge(actual, EdgeType.HAS_CITATION, claim_key, citation_key) and self._edge(
                actual, EdgeType.CITES_CHUNK, citation_key, chunk_key
            ):
                citation_path_count += 1
                document_key = node_id(NodeType.DOCUMENT, row.document_id)
                relation_group = relation.source_group_id if relation else None
                if (
                    self._edge(actual, EdgeType.PART_OF_DOCUMENT, chunk_key, document_key)
                    and relation_group
                    and self._edge(
                        actual, EdgeType.MEMBER_OF_SOURCE_GROUP,
                        document_key, node_id(NodeType.SOURCE_GROUP, relation_group),
                    )
                ):
                    source_group_path_count += 1
            else:
                issue("MISSING_CITATION_PATH", citation_id=row.id)

        cross_job = 0
        for row in state.claims:
            if row.research_job_id != state.research.id or row.evidence_job_id != state.evidence.id:
                cross_job += 1
                issue("CROSS_JOB_CLAIM", claim_id=row.id)
            sub = subquestions.get(row.subquestion_id)
            if sub is None or sub.research_job_id != state.research.id:
                issue("CLAIM_WRONG_SUBQUESTION", claim_id=row.id, subquestion_id=row.subquestion_id)
            key = node_id(NodeType.CLAIM, row.id)
            node = actual.nodes.get(key)
            if node and node["properties"].get("status") != row.status:
                issue("CLAIM_STATUS_MODIFIED", claim_id=row.id)
            if node and node["properties"].get("confidence_tier") != row.confidence_tier:
                issue("CLAIM_CONFIDENCE_MODIFIED", claim_id=row.id)
            if not self._edge(
                actual,
                EdgeType.HAS_CLAIM,
                node_id(NodeType.SUBQUESTION, row.subquestion_id),
                key,
            ):
                issue("MISSING_CLAIM_PROVENANCE", claim_id=row.id)
        for row in state.chunks:
            if row.research_job_id != state.research.id:
                cross_job += 1
                issue("CROSS_JOB_CHUNK", chunk_id=row.id)
        for row in state.documents:
            if row.research_job_id != state.research.id:
                cross_job += 1
                issue("CROSS_JOB_DOCUMENT", document_id=row.id)
        for row in state.sources:
            if row.research_job_id != state.research.id:
                cross_job += 1
                issue("CROSS_JOB_CHUNK_SOURCE", chunk_source_id=row.id)
        for row in state.groups:
            if row.evidence_job_id != state.evidence.id:
                cross_job += 1
                issue("CROSS_JOB_SOURCE_GROUP", source_group_id=row.id)
            for document_id in json.loads(row.document_ids_json):
                if document_id not in documents:
                    issue("GROUP_DOCUMENT_MISSING", source_group_id=row.id, document_id=document_id)
                if not self._edge(
                    actual,
                    EdgeType.MEMBER_OF_SOURCE_GROUP,
                    node_id(NodeType.DOCUMENT, document_id),
                    node_id(NodeType.SOURCE_GROUP, row.id),
                ):
                    issue(
                        "MISSING_GROUP_MEMBERSHIP", source_group_id=row.id, document_id=document_id
                    )
        for row in state.origins:
            if row.claim_id not in claims or row.chunk_id not in chunks:
                issue("INVALID_CLAIM_ORIGIN", origin_id=row.id)
            if (row.chunk_id, row.document_id) not in source_pairs:
                issue("ORIGIN_DOCUMENT_MISMATCH", origin_id=row.id)
            if not self._edge(
                actual,
                EdgeType.ORIGINATED_FROM,
                node_id(NodeType.CLAIM, row.claim_id),
                node_id(NodeType.EVIDENCE_CHUNK, row.chunk_id),
            ):
                issue("MISSING_CLAIM_ORIGIN", origin_id=row.id)
        relation_types = {
            "DIRECT_SUPPORT": EdgeType.SUPPORTED_BY,
            "PARTIAL_SUPPORT": EdgeType.PARTIALLY_SUPPORTED_BY,
            "CONTRADICTION": EdgeType.CONTRADICTED_BY,
        }
        for row in state.relations:
            if (
                row.claim_id not in claims
                or row.chunk_id not in chunks
                or row.document_id not in documents
            ):
                issue("INVALID_RELATION_ENTITY", relation_id=row.id)
            if row.source_group_id not in groups:
                issue("RELATION_GROUP_MISSING", relation_id=row.id)
            if (row.chunk_id, row.document_id) not in source_pairs:
                issue("RELATION_DOCUMENT_MISMATCH", relation_id=row.id)
            kind = relation_types.get(row.adjudicated_relation)
            if (
                kind
                and (
                    row.id in cited_relation_ids
                    or any(
                        edge["properties"].get("claim_evidence_relation_id") == row.id
                        for edge in actual.edges.values()
                    )
                )
                and not self._edge(
                    actual,
                    kind,
                    node_id(NodeType.CLAIM, row.claim_id),
                    node_id(NodeType.EVIDENCE_CHUNK, row.chunk_id),
                    source_entity_id=row.id,
                )
            ):
                issue("MISSING_RELATION_EDGE", relation_id=row.id)

        orphan_nodes = []
        root = node_id(NodeType.RESEARCH, state.research.id)
        for key, node in actual.nodes.items():
            kind = node["type"]
            if kind == NodeType.RESEARCH:
                continue
            expected_in = {
                NodeType.SUBQUESTION: {EdgeType.HAS_SUBQUESTION},
                NodeType.CLAIM: {EdgeType.HAS_CLAIM},
                NodeType.CITATION: {EdgeType.HAS_CITATION},
                NodeType.EVIDENCE_CHUNK: {
                    EdgeType.ORIGINATED_FROM,
                    EdgeType.SUPPORTED_BY,
                    EdgeType.PARTIALLY_SUPPORTED_BY,
                    EdgeType.CONTRADICTED_BY,
                    EdgeType.CITES_CHUNK,
                },
                NodeType.SOURCE_GROUP: {EdgeType.MEMBER_OF_SOURCE_GROUP},
            }.get(kind)
            if expected_in and not any(
                edge["type"] in expected_in for edge in actual.incoming[key]
            ):
                orphan_nodes.append(key)
            if kind == NodeType.DOCUMENT and not actual.incoming[key]:
                orphan_nodes.append(key)

        provenance_count = sum(
            self._edge(
                actual,
                EdgeType.HAS_CLAIM,
                node_id(NodeType.SUBQUESTION, claim.subquestion_id),
                node_id(NodeType.CLAIM, claim.id),
            )
            and self._edge(
                actual,
                EdgeType.HAS_SUBQUESTION,
                root,
                node_id(NodeType.SUBQUESTION, claim.subquestion_id),
            )
            for claim in state.claims
        )
        counts = Counter(node["type"] for node in actual.nodes.values())
        edge_counts = Counter(edge["type"] for edge in actual.edges.values())
        actual_hash = digest(
            {
                "schema_version": 1,
                "nodes": [actual.nodes[key] for key in sorted(actual.nodes)],
                "edges": [actual.edges[key] for key in sorted(actual.edges)],
            }
        )
        if actual_hash != expected.content_hash:
            issue("GRAPH_HASH_MISMATCH", expected=expected.content_hash, actual=actual_hash)
        metrics = {
            "expected_nodes": len(expected_nodes),
            "actual_nodes": len(actual_nodes),
            "expected_edges": len(expected_edges),
            "actual_edges": len(actual_edges),
            "node_coverage": len(expected_nodes & actual_nodes) / len(expected_nodes)
            if expected_nodes
            else 1.0,
            "edge_coverage": len(expected_edges & actual_edges) / len(expected_edges)
            if expected_edges
            else 1.0,
            "claim_provenance_path_coverage": provenance_count / len(state.claims)
            if state.claims
            else 1.0,
            "citation_path_coverage": citation_path_count / valid_citation_count
            if valid_citation_count
            else 1.0,
            "source_group_path_coverage": source_group_path_count / valid_citation_count
            if valid_citation_count
            else 1.0,
            "orphan_node_count": len(set(orphan_nodes)),
            "orphan_edge_count": len(set(orphan_edges)),
            "cross_job_leakage_count": cross_job,
            "connected_components": len(actual.connected_components()),
            "nodes_by_type": dict(sorted(counts.items())),
            "edges_by_type": dict(sorted(edge_counts.items())),
            "content_hash": actual_hash,
            "missing_nodes": sorted(expected_nodes - actual_nodes),
            "unexpected_nodes": sorted(actual_nodes - expected_nodes),
            "missing_edges": sorted(expected_edges - actual_edges),
            "unexpected_edges": sorted(actual_edges - expected_edges),
            "orphan_edges": sorted(set(orphan_edges)),
            "citation_path_errors": [x for x in issues if x["code"] == "MISSING_CITATION_PATH"],
            "cross_job_errors": [x for x in issues if x["code"].startswith("CROSS_JOB")],
        }
        return {"status": "VALID" if not issues else "INVALID", "issues": issues, **metrics}

    @staticmethod
    def _edge(
        graph: GraphProjection,
        kind: EdgeType,
        source: str,
        target: str,
        source_entity_id: int | None = None,
    ) -> bool:
        return any(
            edge["type"] == kind
            and edge["target"] == target
            and (
                source_entity_id is None
                or edge["properties"].get("claim_evidence_relation_id") == source_entity_id
            )
            for edge in graph.outgoing[source]
        )
