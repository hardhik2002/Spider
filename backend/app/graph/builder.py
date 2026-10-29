"""Pure, deterministic graph materialization from authoritative Phase 1-6 rows."""

import hashlib
import json
import time
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import ResearchJob, ResearchSubquestion
from app.evidence.logic import validate_citation
from app.evidence.models import (
    ClaimCitation,
    ClaimEvidenceRelation,
    ClaimOrigin,
    EvidenceJob,
    SourceGroup,
    VerifiedClaim,
)
from app.graph.types import GRAPH_SCHEMA_VERSION, EdgeType, NodeType
from app.rag.models import KnowledgeChunk, KnowledgeChunkSource, KnowledgeDocument


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def row_data(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def node_id(kind: NodeType, entity_id: str | int) -> str:
    return f"{kind.value.lower()}:{entity_id}"


def edge_id(kind: EdgeType, source: str, target: str, entity_id: str | int) -> str:
    return "edge:" + digest([kind.value, source, target, str(entity_id)])


@dataclass
class SourceState:
    research: ResearchJob
    evidence: EvidenceJob
    subquestions: list[ResearchSubquestion]
    claims: list[VerifiedClaim]
    origins: list[ClaimOrigin]
    relations: list[ClaimEvidenceRelation]
    citations: list[ClaimCitation]
    groups: list[SourceGroup]
    chunks: list[KnowledgeChunk]
    sources: list[KnowledgeChunkSource]
    documents: list[KnowledgeDocument]

    def fingerprint(self, include_uncited_relations: bool) -> str:
        data = {
            name: [row_data(row) for row in getattr(self, name)]
            for name in (
                "subquestions",
                "claims",
                "origins",
                "relations",
                "citations",
                "groups",
                "chunks",
                "sources",
                "documents",
            )
        }
        data["research"] = row_data(self.research)
        data["evidence"] = row_data(self.evidence)
        data["graph_schema_version"] = GRAPH_SCHEMA_VERSION
        data["include_uncited_relations"] = include_uncited_relations
        return digest(data)


@dataclass
class GraphMaterial:
    nodes: dict[str, dict]
    edges: dict[str, dict]
    content_hash: str
    timings: dict[str, int]

    def export(self, snapshot_id: str, research_job_id: str, evidence_job_id: str) -> dict:
        return {
            "graph": {
                "snapshot_id": snapshot_id,
                "research_job_id": research_job_id,
                "evidence_job_id": evidence_job_id,
                "schema_version": GRAPH_SCHEMA_VERSION,
                "graph_content_hash": self.content_hash,
            },
            "nodes": [self.nodes[key] for key in sorted(self.nodes)],
            "edges": [self.edges[key] for key in sorted(self.edges)],
        }


class GraphBuilder:
    def __init__(self, sessions) -> None:
        self.sessions = sessions

    async def load(self, research_job_id: str, evidence_job_id: str) -> SourceState:
        async with self.sessions() as session:
            research = await session.get(ResearchJob, research_job_id)
            evidence = await session.get(EvidenceJob, evidence_job_id)
            if research is None or evidence is None or evidence.research_job_id != research_job_id:
                raise KeyError("Research/evidence job pair not found")
            if evidence.status not in {"COMPLETED", "PARTIAL"}:
                raise ValueError("Evidence job must have finished before graph materialization")

            async def rows(model, predicate):
                return list(
                    (
                        await session.execute(select(model).where(predicate).order_by(model.id))
                    ).scalars()
                )

            subquestions = await rows(
                ResearchSubquestion, ResearchSubquestion.research_job_id == research_job_id
            )
            claims = await rows(VerifiedClaim, VerifiedClaim.evidence_job_id == evidence_job_id)
            claim_ids = [row.id for row in claims]
            origins = (
                await rows(ClaimOrigin, ClaimOrigin.claim_id.in_(claim_ids)) if claim_ids else []
            )
            relations = (
                await rows(ClaimEvidenceRelation, ClaimEvidenceRelation.claim_id.in_(claim_ids))
                if claim_ids
                else []
            )
            citations = (
                await rows(ClaimCitation, ClaimCitation.claim_id.in_(claim_ids))
                if claim_ids
                else []
            )
            groups = await rows(SourceGroup, SourceGroup.evidence_job_id == evidence_job_id)
            chunk_ids = {row.chunk_id for row in origins + relations + citations}
            chunks = (
                await rows(KnowledgeChunk, KnowledgeChunk.id.in_(chunk_ids)) if chunk_ids else []
            )
            sources = (
                await rows(KnowledgeChunkSource, KnowledgeChunkSource.chunk_id.in_(chunk_ids))
                if chunk_ids
                else []
            )
            document_ids = {row.document_id for row in origins + relations + citations + sources}
            for group in groups:
                document_ids.update(json.loads(group.document_ids_json))
            documents = (
                await rows(KnowledgeDocument, KnowledgeDocument.id.in_(document_ids))
                if document_ids
                else []
            )
        return SourceState(
            research,
            evidence,
            subquestions,
            claims,
            origins,
            relations,
            citations,
            groups,
            chunks,
            sources,
            documents,
        )

    def build(self, state: SourceState, *, include_uncited_relations: bool) -> GraphMaterial:
        started = time.monotonic()
        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        chunks = {row.id: row for row in state.chunks}
        relations = {row.id: row for row in state.relations}
        citations_by_relation: dict[int, list[ClaimCitation]] = {}
        for citation in state.citations:
            citations_by_relation.setdefault(citation.relation_id, []).append(citation)

        def add_node(kind: NodeType, entity_id: str | int, label: str, properties: dict) -> str:
            key = node_id(kind, entity_id)
            nodes[key] = {
                "id": key,
                "type": kind.value,
                "label": " ".join(label.split())[:160],
                "properties": properties,
            }
            return key

        def add_edge(
            kind: EdgeType,
            source: str,
            target: str,
            entity_id: str | int,
            properties: dict | None = None,
        ) -> None:
            key = edge_id(kind, source, target, entity_id)
            edges[key] = {
                "id": key,
                "type": kind.value,
                "source": source,
                "target": target,
                "properties": {"source_entity_id": str(entity_id), **(properties or {})},
            }

        root = add_node(
            NodeType.RESEARCH,
            state.research.id,
            state.research.question,
            {
                "research_job_id": state.research.id,
                "question": state.research.question[:2000],
                "status": state.research.status,
                "created_at": str(state.research.created_at),
            },
        )
        for row in state.subquestions:
            key = add_node(
                NodeType.SUBQUESTION,
                row.id,
                row.question,
                {
                    "subquestion_id": row.id,
                    "plan_id": row.plan_id,
                    "question": row.question[:500],
                    "priority": row.priority,
                    "status": row.status,
                    "order": row.order,
                },
            )
            add_edge(EdgeType.HAS_SUBQUESTION, root, key, row.id)

        for row in state.claims:
            key = add_node(
                NodeType.CLAIM,
                row.id,
                row.claim_text,
                {
                    "claim_id": row.id,
                    "subquestion_id": row.subquestion_id,
                    "claim_text": row.claim_text[:1500],
                    "normalized_claim": row.normalized_claim[:1500],
                    "claim_type": row.claim_type,
                    "status": row.status,
                    "confidence_tier": row.confidence_tier,
                    "support_count": row.support_count,
                    "contradiction_count": row.contradiction_count,
                    "partial_count": row.partial_count,
                    "independent_support_groups": row.independent_support_groups,
                    "independent_contradiction_groups": row.independent_contradiction_groups,
                    "needs_more_verification": row.needs_more_verification,
                },
            )
            add_edge(
                EdgeType.HAS_CLAIM,
                node_id(NodeType.SUBQUESTION, row.subquestion_id),
                key,
                row.id,
            )

        for row in state.chunks:
            add_node(
                NodeType.EVIDENCE_CHUNK,
                row.id,
                row.heading or row.source_title or f"Chunk {row.id}",
                {
                    "chunk_id": row.id,
                    "chunk_index": row.chunk_index,
                    "heading": row.heading,
                    "text_hash": row.text_hash,
                    "source_title": row.source_title,
                    "source_url": row.source_url,
                    "source_domain": row.source_domain,
                    "token_count": row.token_count,
                },
            )
        for row in state.documents:
            add_node(
                NodeType.DOCUMENT,
                row.id,
                row.title or row.source_domain,
                {
                    "document_id": row.id,
                    "title": row.title,
                    "canonical_url": row.canonical_url,
                    "source_url": row.source_url,
                    "source_domain": row.source_domain,
                    "content_hash": row.content_hash,
                },
            )
        for row in state.groups:
            member_ids = json.loads(row.document_ids_json)
            add_node(
                NodeType.SOURCE_GROUP,
                row.id,
                f"Source group {row.id}",
                {
                    "source_group_id": row.id,
                    "group_key": row.group_key,
                    "grouping_method": None,
                    "member_count": len(member_ids),
                    "source_type": row.source_type,
                },
            )
            for document_id in member_ids:
                add_edge(
                    EdgeType.MEMBER_OF_SOURCE_GROUP,
                    node_id(NodeType.DOCUMENT, document_id),
                    node_id(NodeType.SOURCE_GROUP, row.id),
                    f"{row.id}:{document_id}",
                )

        for row in state.sources:
            add_edge(
                EdgeType.PART_OF_DOCUMENT,
                node_id(NodeType.EVIDENCE_CHUNK, row.chunk_id),
                node_id(NodeType.DOCUMENT, row.document_id),
                row.id,
                {"chunk_source_id": row.id, "chunk_index": row.chunk_index},
            )
        for row in state.origins:
            add_edge(
                EdgeType.ORIGINATED_FROM,
                node_id(NodeType.CLAIM, row.claim_id),
                node_id(NodeType.EVIDENCE_CHUNK, row.chunk_id),
                row.id,
                {"claim_origin_id": row.id, "document_id": row.document_id},
            )

        edge_started = time.monotonic()
        node_build_ms = int((edge_started - started) * 1000)
        relation_types = {
            "DIRECT_SUPPORT": EdgeType.SUPPORTED_BY,
            "PARTIAL_SUPPORT": EdgeType.PARTIALLY_SUPPORTED_BY,
            "CONTRADICTION": EdgeType.CONTRADICTED_BY,
        }
        for row in state.relations:
            kind = relation_types.get(row.adjudicated_relation)
            if kind is None:
                continue
            valid_citations = [
                c
                for c in citations_by_relation.get(row.id, [])
                if c.claim_id == row.claim_id
                and c.chunk_id == row.chunk_id
                and c.document_id == row.document_id
                and c.source_url == row.source_url
                and row.chunk_id in chunks
                and validate_citation(
                    chunks[row.chunk_id].text, c.start_offset, c.end_offset, c.exact_text
                )
            ]
            if not include_uncited_relations and not valid_citations:
                continue
            add_edge(
                kind,
                node_id(NodeType.CLAIM, row.claim_id),
                node_id(NodeType.EVIDENCE_CHUNK, row.chunk_id),
                row.id,
                {
                    "claim_evidence_relation_id": row.id,
                    "document_id": row.document_id,
                    "nli_relation": row.nli_relation,
                    "entailment_score": row.entailment_score,
                    "neutral_score": row.neutral_score,
                    "contradiction_score": row.contradiction_score,
                    "nli_model_name": row.nli_model_name,
                    "adjudicated_relation": row.adjudicated_relation,
                    "important_qualifier_mismatch": row.qualifier_mismatch,
                    "retrieval_rank": row.retrieval_rank,
                    "retrieval_score": row.retrieval_score,
                    "citation_valid": bool(valid_citations),
                    "source_group_id": row.source_group_id,
                },
            )

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
            key = add_node(
                NodeType.CITATION,
                row.id,
                row.exact_text,
                {
                    "citation_id": row.id,
                    "relation_id": row.relation_id,
                    "claim_id": row.claim_id,
                    "chunk_id": row.chunk_id,
                    "document_id": row.document_id,
                    "relation": row.relation,
                    "start_offset": row.start_offset,
                    "end_offset": row.end_offset,
                    "exact_text": row.exact_text,
                    "source_url": row.source_url,
                    "valid": valid,
                },
            )
            add_edge(EdgeType.HAS_CITATION, node_id(NodeType.CLAIM, row.claim_id), key, row.id)
            add_edge(
                EdgeType.CITES_CHUNK,
                key,
                node_id(NodeType.EVIDENCE_CHUNK, row.chunk_id),
                row.id,
                {"document_id": row.document_id},
            )

        content_hash = digest(
            {
                "schema_version": GRAPH_SCHEMA_VERSION,
                "nodes": [nodes[key] for key in sorted(nodes)],
                "edges": [edges[key] for key in sorted(edges)],
            }
        )
        return GraphMaterial(
            nodes,
            edges,
            content_hash,
            {
                "node_build_ms": node_build_ms,
                "edge_build_ms": int((time.monotonic() - edge_started) * 1000),
            },
        )
