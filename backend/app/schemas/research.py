from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ResearchRequest(BaseModel):
    question: str = Field(min_length=8, max_length=2000)
    max_subquestions: int = Field(default=6, ge=1, le=10)
    search_queries_per_subquestion: int = Field(default=3, ge=1, le=5)
    search_results_per_query: int = Field(default=8, ge=1, le=20)
    seeds_per_subquestion: int = Field(default=3, ge=1, le=10)
    max_pages_per_subquestion: int = Field(default=10, ge=1, le=100)
    max_total_pages: int = Field(default=60, ge=1, le=300)
    max_depth: int = Field(default=2, ge=0, le=5)

    @field_validator("question")
    @classmethod
    def question_has_words(cls, value: str) -> str:
        if len(value.split()) < 2:
            raise ValueError("question must contain at least two words")
        return value


class PlannedSearchQuery(BaseModel):
    query: str = Field(min_length=3, max_length=200)
    intent: str = Field(min_length=3, max_length=300)


class PlannedSubquestion(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=8, max_length=500)
    rationale: str = Field(min_length=3, max_length=600)
    priority: Literal["high", "medium", "low"]
    expected_evidence: list[str] = Field(min_length=1, max_length=6)
    preferred_source_types: list[str] = Field(min_length=1, max_length=6)
    search_queries: list[PlannedSearchQuery] = Field(min_length=1, max_length=5)


class ResearchPlan(BaseModel):
    original_question: str
    normalized_question: str = Field(min_length=8, max_length=2000)
    objective: str = Field(min_length=8, max_length=2000)
    assumptions: list[str] = Field(max_length=10)
    scope_inclusions: list[str] = Field(max_length=10)
    scope_exclusions: list[str] = Field(max_length=10)
    time_sensitivity: Literal["evergreen", "recent", "historical", "mixed"]
    subquestions: list[PlannedSubquestion] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_subquestion_ids(self) -> "ResearchPlan":
        ids = [item.id for item in self.subquestions]
        if len(ids) != len(set(ids)):
            raise ValueError("subquestion IDs must be unique")
        return self


def validate_plan(plan: ResearchPlan, request: ResearchRequest) -> ResearchPlan:
    if plan.original_question != request.question:
        raise ValueError("planner changed the original question")
    if len(plan.subquestions) > request.max_subquestions:
        raise ValueError("planner exceeded max_subquestions")
    for subquestion in plan.subquestions:
        if len(subquestion.search_queries) > request.search_queries_per_subquestion:
            raise ValueError("planner exceeded search_queries_per_subquestion")
    return plan
