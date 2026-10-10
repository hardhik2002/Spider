"""Inspectable Phase 6 evidence ledger tables."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, utc_now


class EvidenceJob(Base):
    __tablename__ = "evidence_jobs"
    __table_args__ = (UniqueConstraint("research_job_id", "input_fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id"), index=True)
    agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    stage: Mapped[str] = mapped_column(String(40), default="PENDING")
    counters_json: Mapped[str] = mapped_column(Text, default="{}")
    timings_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class VerifiedClaim(Base):
    __tablename__ = "verified_claims"
    __table_args__ = (UniqueConstraint("evidence_job_id", "subquestion_id", "stable_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stable_key: Mapped[str] = mapped_column(String(64), index=True)
    evidence_job_id: Mapped[str] = mapped_column(ForeignKey("evidence_jobs.id"), index=True)
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id"), index=True)
    subquestion_id: Mapped[int] = mapped_column(ForeignKey("research_subquestions.id"), index=True)
    claim_text: Mapped[str] = mapped_column(Text)
    normalized_claim: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(30))
    subject: Mapped[str | None] = mapped_column(Text)
    predicate: Mapped[str | None] = mapped_column(Text)
    object: Mapped[str | None] = mapped_column(Text)
    qualifiers_json: Mapped[str] = mapped_column(Text, default="[]")
    conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    temporal_scope: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="INSUFFICIENT_EVIDENCE")
    confidence_tier: Mapped[str] = mapped_column(String(20), default="UNRESOLVED")
    support_count: Mapped[int] = mapped_column(Integer, default=0)
    contradiction_count: Mapped[int] = mapped_column(Integer, default=0)
    partial_count: Mapped[int] = mapped_column(Integer, default=0)
    independent_support_groups: Mapped[int] = mapped_column(Integer, default=0)
    independent_contradiction_groups: Mapped[int] = mapped_column(Integer, default=0)
    needs_more_verification: Mapped[bool] = mapped_column(Boolean, default=True)
    verification_gap_reason: Mapped[str | None] = mapped_column(Text)
    decision_metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClaimOrigin(Base):
    __tablename__ = "claim_origins"
    __table_args__ = (UniqueConstraint("claim_id", "chunk_id", "document_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("verified_claims.id"), index=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("knowledge_chunks.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    source_url: Mapped[str] = mapped_column(Text)


class SourceGroup(Base):
    __tablename__ = "source_groups"
    __table_args__ = (UniqueConstraint("evidence_job_id", "group_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_job_id: Mapped[str] = mapped_column(ForeignKey("evidence_jobs.id"), index=True)
    group_key: Mapped[str] = mapped_column(String(64))
    document_ids_json: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClaimEvidenceRelation(Base):
    __tablename__ = "claim_evidence_relations"
    __table_args__ = (UniqueConstraint("claim_id", "chunk_id", "document_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("verified_claims.id"), index=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("knowledge_chunks.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    source_group_id: Mapped[str] = mapped_column(ForeignKey("source_groups.id"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_domain: Mapped[str] = mapped_column(String(255))
    retrieval_rank: Mapped[int] = mapped_column(Integer)
    retrieval_score: Mapped[float | None] = mapped_column(Float)
    nli_relation: Mapped[str] = mapped_column(String(20))
    entailment_score: Mapped[float] = mapped_column(Float)
    neutral_score: Mapped[float] = mapped_column(Float)
    contradiction_score: Mapped[float] = mapped_column(Float)
    nli_model_name: Mapped[str] = mapped_column(Text)
    adjudicated_relation: Mapped[str] = mapped_column(String(25))
    adjudication_summary: Mapped[str | None] = mapped_column(Text)
    qualifier_mismatch: Mapped[bool] = mapped_column(Boolean, default=False)
    is_citation_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClaimCitation(Base):
    __tablename__ = "claim_citations"
    __table_args__ = (UniqueConstraint("relation_id", "start_offset", "end_offset"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("verified_claims.id"), index=True)
    relation_id: Mapped[int] = mapped_column(ForeignKey("claim_evidence_relations.id"), index=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("knowledge_chunks.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    exact_text: Mapped[str] = mapped_column(Text)
    relation: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
