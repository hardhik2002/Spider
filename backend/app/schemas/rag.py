from typing import Literal

from pydantic import BaseModel, Field


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    retrieval_mode: Literal["dense", "lexical", "hybrid"] = "hybrid"
    dense_top_k: int | None = Field(default=None, ge=1, le=200)
    lexical_top_k: int | None = Field(default=None, ge=1, le=200)
    fusion_top_k: int | None = Field(default=None, ge=1, le=100)
    final_top_k: int | None = Field(default=None, ge=1, le=50)
    rerank: bool | None = None
    include_neighbor_context: bool | None = None
    subquestion_id: str | None = Field(default=None, max_length=80)
    source_domain: str | None = Field(default=None, max_length=255)
    document_id: int | None = Field(default=None, ge=1)
    crawl_job_id: str | None = Field(default=None, max_length=36)
