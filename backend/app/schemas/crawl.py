from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.crawler.models import JobStatus
from app.crawler.normalizer import InvalidURL, normalize_url


class CrawlRequest(BaseModel):
    start_url: str
    max_depth: int = Field(default=2, ge=0, le=10)
    max_pages: int = Field(default=25, ge=1, le=500)
    timeout: float = Field(default=120.0, gt=0, le=3600)
    max_content_size: int = Field(default=2_000_000, ge=1024, le=10_000_000)
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    allow_external_domains: bool = False

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
