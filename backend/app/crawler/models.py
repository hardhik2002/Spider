from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PageStatus(StrEnum):
    QUEUED = "QUEUED"
    FETCHING = "FETCHING"
    PARSED = "PARSED"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class CrawlMode(StrEnum):
    FIFO = "fifo"
    INTELLIGENT = "intelligent"


class ScoringStatus(StrEnum):
    NOT_SCORED = "NOT_SCORED"
    SCORED = "SCORED"


class RejectionReason(StrEnum):
    LOW_RELEVANCE = "LOW_RELEVANCE"
    EXTERNAL_DOMAIN_DISABLED = "EXTERNAL_DOMAIN_DISABLED"
    DOMAIN_NOT_ALLOWED = "DOMAIN_NOT_ALLOWED"
    DEPTH_LIMIT = "DEPTH_LIMIT"
    DUPLICATE_URL = "DUPLICATE_URL"
    PAGE_LIMIT = "PAGE_LIMIT"
    ROBOTS_DENIED = "ROBOTS_DENIED"
    SSRF_BLOCKED = "SSRF_BLOCKED"


@dataclass(frozen=True)
class FrontierItem:
    url: str
    normalized_url: str
    depth: int
    parent_url: str | None = None
    discovered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    priority: float = 0.0
    discovery_order: int = 0
    relevance_score: float | None = None
    priority_score: float | None = None
    anchor_text: str | None = None
    link_context: str | None = None
    source_link_id: int | None = None


@dataclass(frozen=True)
class FetchResult:
    final_url: str
    status_code: int
    content_type: str
    response_time_ms: int
    response_size: int
    body: bytes


@dataclass(frozen=True)
class Link:
    target_url: str
    normalized_target_url: str
    anchor_text: str
    is_internal: bool
    surrounding_text: str = ""


@dataclass(frozen=True)
class ParsedPage:
    title: str | None
    text_content: str
    meta_description: str | None
    canonical_url: str | None
    links: list[Link]
