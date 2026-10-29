"""Materialized graph tables. Phase 1-6 domain rows remain authoritative."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, utc_now


class GraphSnapshot(Base):
    __tablename__ = "graph_snapshots"
    __table_args__ = (UniqueConstraint("research_job_id", "evidence_job_id", "input_fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id"), index=True)
    evidence_job_id: Mapped[str] = mapped_column(ForeignKey("evidence_jobs.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    graph_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    include_uncited_relations: Mapped[bool] = mapped_column(Boolean, default=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    graph_content_hash: Mapped[str | None] = mapped_column(String(64))
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, default=0)
    nodes_by_type_json: Mapped[str] = mapped_column(Text, default="{}")
    edges_by_type_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_status: Mapped[str] = mapped_column(String(20), default="PARTIAL")
    validation_issues_json: Mapped[str] = mapped_column(Text, default="[]")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    timings_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class GraphBuildJob(Base):
    __tablename__ = "graph_build_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id"), index=True)
    evidence_job_id: Mapped[str] = mapped_column(ForeignKey("evidence_jobs.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("graph_snapshots.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    nodes_created: Mapped[int] = mapped_column(Integer, default=0)
    edges_created: Mapped[int] = mapped_column(Integer, default=0)
    nodes_reused: Mapped[int] = mapped_column(Integer, default=0)
    edges_reused: Mapped[int] = mapped_column(Integer, default=0)
    validation_errors: Mapped[int] = mapped_column(Integer, default=0)
    timings_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)


class GraphNode(Base):
    __tablename__ = "graph_nodes"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "node_id"),
        Index("ix_graph_nodes_snapshot_type", "snapshot_id", "node_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("graph_snapshots.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(100))
    node_type: Mapped[str] = mapped_column(String(30))
    source_entity_id: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(180))
    properties_json: Mapped[str] = mapped_column(Text, default="{}")


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "edge_id"),
        Index("ix_graph_edges_snapshot_type", "snapshot_id", "edge_type"),
        Index("ix_graph_edges_source", "snapshot_id", "source_node_id"),
        Index("ix_graph_edges_target", "snapshot_id", "target_node_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("graph_snapshots.id"), index=True)
    edge_id: Mapped[str] = mapped_column(String(70))
    edge_type: Mapped[str] = mapped_column(String(40))
    source_node_id: Mapped[str] = mapped_column(String(100))
    target_node_id: Mapped[str] = mapped_column(String(100))
    source_entity_id: Mapped[str] = mapped_column(String(80))
    properties_json: Mapped[str] = mapped_column(Text, default="{}")
