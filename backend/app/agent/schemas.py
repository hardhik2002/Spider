from typing import Literal

from pydantic import BaseModel, Field

GapType = Literal[
    "missing_definition",
    "missing_comparison",
    "missing_metric",
    "missing_primary_evidence",
    "missing_limitations",
    "missing_recent_evidence",
    "insufficient_source_diversity",
    "other",
]


class AgentRequest(BaseModel):
    max_iterations: int = Field(default=5, ge=0, le=20)
    max_new_search_queries: int = Field(default=12, ge=0, le=100)
    max_new_seeds: int = Field(default=10, ge=0, le=100)
    max_new_pages: int = Field(default=40, ge=0, le=300)
    max_runtime_seconds: int = Field(default=900, ge=1, le=7200)
    min_evidence_chunks_per_subquestion: int = Field(default=3, ge=1, le=20)
    min_unique_sources_per_subquestion: int = Field(default=2, ge=1, le=20)
    retrieval_top_k: int = Field(default=8, ge=1, le=50)
    rerank: bool = True
    max_queries_per_gap: int = Field(default=2, ge=1, le=5)
    max_assessment_chunks: int = Field(default=6, ge=1, le=12)
    max_assessment_chars_per_chunk: int = Field(default=1200, ge=100, le=4000)
    max_total_assessment_chars: int = Field(default=7000, ge=500, le=20000)


class AssessmentResult(BaseModel):
    subquestion_id: str
    coverage: Literal["insufficient", "partial", "sufficient"]
    evidence_summary: str = Field(max_length=1200)
    missing_aspects: list[str] = Field(max_length=8)
    needs_more_research: bool
    suggested_gap_types: list[GapType] = Field(max_length=8)


class GapQueryPlan(BaseModel):
    gap_id: str
    queries: list[str] = Field(max_length=5)
    search_intent: str = Field(max_length=500)
    desired_evidence: list[str] = Field(max_length=6)
