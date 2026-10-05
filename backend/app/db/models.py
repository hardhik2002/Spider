from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.crawler.models import CrawlMode, JobStatus, PageStatus, ScoringStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    start_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_depth: Mapped[int] = mapped_column(Integer)
    max_pages: Mapped[int] = mapped_column(Integer)
    timeout: Mapped[float] = mapped_column()
    max_content_size: Mapped[int] = mapped_column(Integer)
    allowed_domains: Mapped[str] = mapped_column(Text, default="[]")
    allow_external_domains: Mapped[bool] = mapped_column(Boolean, default=False)
    pages_discovered: Mapped[int] = mapped_column(Integer, default=0)
    pages_crawled: Mapped[int] = mapped_column(Integer, default=0)
    pages_failed: Mapped[int] = mapped_column(Integer, default=0)
    pages_skipped: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    research_query: Mapped[str | None] = mapped_column(Text)
    crawl_mode: Mapped[str] = mapped_column(String(20), default=CrawlMode.FIFO.value)
    min_relevance_score: Mapped[float | None] = mapped_column(Float)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    depth_penalty: Mapped[float | None] = mapped_column(Float)
    exploration_rate: Mapped[float | None] = mapped_column(Float)
    links_scored: Mapped[int] = mapped_column(Integer, default=0)
    links_below_threshold: Mapped[int] = mapped_column(Integer, default=0)
    relevance_score_sum: Mapped[float] = mapped_column(Float, default=0.0)
    highest_relevance_score: Mapped[float | None] = mapped_column(Float)
    query_embedding_ms: Mapped[int | None] = mapped_column(Integer)
    candidate_embeddings: Mapped[int] = mapped_column(Integer, default=0)
    candidate_scoring_ms: Mapped[int] = mapped_column(Integer, default=0)
    model_load_ms: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class CrawledPage(Base):
    __tablename__ = "crawled_pages"
    __table_args__ = (
        UniqueConstraint("crawl_job_id", "normalized_url"),
        Index("ix_crawled_pages_job_hash", "crawl_job_id", "content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    crawl_job_id: Mapped[str] = mapped_column(ForeignKey("crawl_jobs.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    normalized_url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str | None] = mapped_column(Text)
    parent_url: Mapped[str | None] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    meta_description: Mapped[str | None] = mapped_column(Text)
    text_content: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(200))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    duplicate_of_page_id: Mapped[int | None] = mapped_column(ForeignKey("crawled_pages.id"))
    response_size: Mapped[int | None] = mapped_column(Integer)
    response_time_ms: Mapped[int | None] = mapped_column(Integer)
    crawl_status: Mapped[str] = mapped_column(String(20), default=PageStatus.QUEUED.value)
    error_message: Mapped[str | None] = mapped_column(Text)
    crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    predicted_link_relevance: Mapped[float | None] = mapped_column(Float)
    actual_page_relevance: Mapped[float | None] = mapped_column(Float)
    priority_score: Mapped[float | None] = mapped_column(Float)
    discovery_order: Mapped[int | None] = mapped_column(Integer)
    source_link_id: Mapped[int | None] = mapped_column(ForeignKey("discovered_links.id"))


class DiscoveredLink(Base):
    __tablename__ = "discovered_links"
    __table_args__ = (
        Index("ix_discovered_links_job_target", "crawl_job_id", "normalized_target_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    crawl_job_id: Mapped[str] = mapped_column(ForeignKey("crawl_jobs.id"), index=True)
    source_page_id: Mapped[int] = mapped_column(ForeignKey("crawled_pages.id"))
    source_url: Mapped[str] = mapped_column(Text)
    target_url: Mapped[str] = mapped_column(Text)
    normalized_target_url: Mapped[str] = mapped_column(Text)
    anchor_text: Mapped[str] = mapped_column(Text)
    is_internal: Mapped[bool] = mapped_column(Boolean)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    source_page_title: Mapped[str | None] = mapped_column(Text)
    link_context: Mapped[str | None] = mapped_column(Text)
    target_depth: Mapped[int | None] = mapped_column(Integer)
    discovery_order: Mapped[int | None] = mapped_column(Integer)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    priority_score: Mapped[float | None] = mapped_column(Float)
    depth_penalty: Mapped[float | None] = mapped_column(Float)
    scoring_status: Mapped[str] = mapped_column(String(20), default=ScoringStatus.NOT_SCORED.value)
    scoring_reason: Mapped[str | None] = mapped_column(Text)
    selected_for_crawl: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(40))
