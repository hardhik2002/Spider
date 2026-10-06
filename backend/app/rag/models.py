from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, utc_now


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("research_job_id", "crawled_page_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id"), index=True)
    crawl_job_id: Mapped[str] = mapped_column(ForeignKey("crawl_jobs.id"), index=True)
    crawled_page_id: Mapped[int] = mapped_column(ForeignKey("crawled_pages.id"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    meta_description: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    chunking_profile: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    source_domain: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    index_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    error_message: Mapped[str | None] = mapped_column(Text)


class KnowledgeChunk(Base):
    """One canonical exact passage per research job; sources retain every occurrence."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("research_job_id", "text_hash"),
        Index("ix_knowledge_chunks_job_document", "research_job_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id"))
    crawl_job_id: Mapped[str] = mapped_column(ForeignKey("crawl_jobs.id"))
    crawled_page_id: Mapped[int] = mapped_column(ForeignKey("crawled_pages.id"))
    source_url: Mapped[str] = mapped_column(Text)
    source_title: Mapped[str | None] = mapped_column(Text)
    source_domain: Mapped[str] = mapped_column(String(255))
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    token_count: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text)
    previous_chunk_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_chunks.id"))
    next_chunk_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_chunks.id"))
    vector_id: Mapped[str] = mapped_column(String(36), unique=True)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeChunkSource(Base):
    __tablename__ = "knowledge_chunk_sources"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index("ix_chunk_sources_chunk_document", "chunk_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("knowledge_chunks.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text)
    previous_source_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_chunk_sources.id"))
    next_source_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_chunk_sources.id"))
    character_start: Mapped[int | None] = mapped_column(Integer)
    character_end: Mapped[int | None] = mapped_column(Integer)


class IndexJob(Base):
    __tablename__ = "index_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    documents_discovered: Mapped[int] = mapped_column(Integer, default=0)
    documents_processed: Mapped[int] = mapped_column(Integer, default=0)
    documents_indexed: Mapped[int] = mapped_column(Integer, default=0)
    documents_skipped: Mapped[int] = mapped_column(Integer, default=0)
    documents_failed: Mapped[int] = mapped_column(Integer, default=0)
    chunks_created: Mapped[int] = mapped_column(Integer, default=0)
    chunks_skipped: Mapped[int] = mapped_column(Integer, default=0)
    chunks_deduplicated: Mapped[int] = mapped_column(Integer, default=0)
    chunks_embedded: Mapped[int] = mapped_column(Integer, default=0)
    vector_records: Mapped[int] = mapped_column(Integer, default=0)
    lexical_records: Mapped[int] = mapped_column(Integer, default=0)
    average_chunk_tokens: Mapped[float | None] = mapped_column(Float)
    median_chunk_tokens: Mapped[float | None] = mapped_column(Float)
    max_chunk_tokens: Mapped[int | None] = mapped_column(Integer)
    embedding_batches: Mapped[int] = mapped_column(Integer, default=0)
    embedding_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    vector_upsert_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    fts_index_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    embedding_model: Mapped[str] = mapped_column(Text)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    chunking_profile: Mapped[str] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)


class IndexDocumentStatus(Base):
    __tablename__ = "index_document_statuses"
    __table_args__ = (UniqueConstraint("index_job_id", "crawled_page_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    index_job_id: Mapped[str] = mapped_column(ForeignKey("index_jobs.id"), index=True)
    crawled_page_id: Mapped[int] = mapped_column(ForeignKey("crawled_pages.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_documents.id"))
    status: Mapped[str] = mapped_column(String(20))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
