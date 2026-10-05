from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.crawler.models import CrawlMode, JobStatus, ScoringStatus
from app.crawler.normalizer import InvalidURL, normalize_url


class CrawlRequest(BaseModel):
    start_url: str
    max_depth: int = Field(default=2, ge=0, le=10)
    max_pages: int = Field(default=25, ge=1, le=500)
    timeout: float = Field(default=120.0, gt=0, le=3600)
    max_content_size: int = Field(default=2_000_000, ge=1024, le=10_000_000)
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    allow_external_domains: bool = False
    crawl_mode: CrawlMode = CrawlMode.FIFO
    research_query: str | None = Field(default=None, max_length=2000)
    min_relevance_score: float | None = Field(default=None, ge=-1, le=1)
    depth_penalty: float | None = Field(default=None, ge=0, le=0.1)
    exploration_rate: float | None = Field(default=None, ge=0, le=1)

    @field_validator("research_query")
    @classmethod
    def normalize_query(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def intelligent_requires_query(self) -> "CrawlRequest":
        if self.crawl_mode == CrawlMode.INTELLIGENT and not self.research_query:
            raise ValueError("research_query is required in intelligent mode")
        if self.crawl_mode == CrawlMode.FIFO and self.min_relevance_score is not None:
            raise ValueError("min_relevance_score requires intelligent mode")
        return self

    @field_validator("start_url")
    @classmethod
    def valid_start_url(cls, value: str) -> str:
        try:
            return normalize_url(value)
        except InvalidURL as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("allowed_domains")
    @classmethod
    def valid_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            if "/" in value or ":" in value or "@" in value or not value.strip():
                raise ValueError("allowed_domains entries must be hostnames")
            try:
                host = normalize_url("https://" + value).split("/")[2]
            except InvalidURL as exc:
                raise ValueError("Invalid allowed domain") from exc
            normalized.append(host)
        return normalized


class CrawlCreated(BaseModel):
    job_id: UUID
    status: JobStatus


class CrawlStatus(BaseModel):
    job_id: UUID
    status: JobStatus
    pages_discovered: int
    pages_crawled: int
    pages_failed: int
    pages_skipped: int
    error_message: str | None = None
    crawl_mode: CrawlMode = CrawlMode.FIFO
    research_query: str | None = None
    links_scored: int | None = None
    links_below_threshold: int | None = None
    average_relevance_score: float | None = None
    highest_relevance_score: float | None = None
    embedding_model: str | None = None
    candidate_embeddings: int | None = None
    query_embedding_ms: int | None = None
    candidate_scoring_ms: int | None = None
    model_load_ms: int | None = None
    duration_ms: int | None = None


class LinkInspection(BaseModel):
    id: int
    source_url: str
    url: str
    normalized_url: str
    anchor_text: str
    source_page_title: str | None
    link_context: str | None
    is_internal: bool
    target_depth: int | None
    discovery_order: int | None
    relevance_score: float | None
    priority_score: float | None
    depth_penalty: float | None
    scoring_status: ScoringStatus
    scoring_reason: str | None
    selected_for_crawl: bool
    rejection_reason: str | None
