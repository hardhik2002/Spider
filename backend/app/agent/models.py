"""Inspectable domain data; LangGraph checkpoints live in a separate SQLite file."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, utc_now
from app.agent.enums import ActionStatus, AgentStatus, GapPriority, GapStatus, StopReason


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id"), index=True)
    status: Mapped[AgentStatus] = mapped_column(
        SqlEnum(AgentStatus, native_enum=False, validate_strings=True), default=AgentStatus.PENDING
    )
    current_node: Mapped[str] = mapped_column(String(40), default="PENDING")
    current_iteration: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[StopReason | None] = mapped_column(
        SqlEnum(StopReason, native_enum=False, validate_strings=True)
    )
    request_json: Mapped[str] = mapped_column(Text)
    queries_used: Mapped[int] = mapped_column(Integer, default=0)
    searches_used: Mapped[int] = mapped_column(Integer, default=0)
    seeds_used: Mapped[int] = mapped_column(Integer, default=0)
    pages_used: Mapped[int] = mapped_column(Integer, default=0)
    pages_crawled: Mapped[int] = mapped_column(Integer, default=0)
    documents_indexed: Mapped[int] = mapped_column(Integer, default=0)
    chunks_indexed: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    llm_failures: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class AgentIteration(Base):
    __tablename__ = "agent_iterations"
    __table_args__ = (UniqueConstraint("agent_run_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[ActionStatus] = mapped_column(
        SqlEnum(ActionStatus, native_enum=False, validate_strings=True),
        default=ActionStatus.RUNNING,
    )
    selected_gap_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str | None] = mapped_column(String(40))
    trace_json: Mapped[str] = mapped_column(Text, default="[]")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchGap(Base):
    __tablename__ = "research_gaps"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "subquestion_id", "gap_type", "description"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id"), index=True)
    subquestion_id: Mapped[int] = mapped_column(ForeignKey("research_subquestions.id"), index=True)
    iteration_created: Mapped[int] = mapped_column(Integer)
    gap_type: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[GapPriority] = mapped_column(
        SqlEnum(GapPriority, native_enum=False, validate_strings=True)
    )
    status: Mapped[GapStatus] = mapped_column(
        SqlEnum(GapStatus, native_enum=False, validate_strings=True), default=GapStatus.OPEN
    )
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_iteration: Mapped[int | None] = mapped_column(Integer)


class EvidenceAssessment(Base):
    __tablename__ = "evidence_assessments"
    __table_args__ = (UniqueConstraint("agent_run_id", "iteration_number", "subquestion_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    iteration_number: Mapped[int] = mapped_column(Integer)
    subquestion_id: Mapped[int] = mapped_column(ForeignKey("research_subquestions.id"), index=True)
    coverage: Mapped[str] = mapped_column(String(20))
    deterministic_minimum_met: Mapped[bool] = mapped_column(Boolean)
    needs_more_research: Mapped[bool] = mapped_column(Boolean)
    evidence_summary: Mapped[str] = mapped_column(Text)
    missing_aspects_json: Mapped[str] = mapped_column(Text)
    suggested_gap_types_json: Mapped[str] = mapped_column(Text)
    evidence_chunk_ids_json: Mapped[str] = mapped_column(Text)
    unique_document_count: Mapped[int] = mapped_column(Integer)
    unique_domain_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentAction(Base):
    __tablename__ = "agent_actions"
    __table_args__ = (UniqueConstraint("agent_run_id", "kind", "action_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    iteration_number: Mapped[int] = mapped_column(Integer)
    gap_id: Mapped[str | None] = mapped_column(ForeignKey("research_gaps.id"))
    kind: Mapped[str] = mapped_column(String(25))
    action_key: Mapped[str] = mapped_column(Text)
    status: Mapped[ActionStatus] = mapped_column(
        SqlEnum(ActionStatus, native_enum=False, validate_strings=True),
        default=ActionStatus.PENDING,
    )
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
