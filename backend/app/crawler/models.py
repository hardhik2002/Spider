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


@dataclass(frozen=True)
class FrontierItem:
    url: str
    normalized_url: str
    depth: int
    parent_url: str | None = None
    discovered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    priority: float = 0.0


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


@dataclass(frozen=True)
class ParsedPage:
    title: str | None
    text_content: str
    meta_description: str | None
    canonical_url: str | None
    links: list[Link]
