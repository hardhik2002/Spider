"""Typed contracts for extraction, verification, and the evidence API."""

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class ClaimType(StrEnum):
    DEFINITION = "DEFINITION"
    DESCRIPTIVE = "DESCRIPTIVE"
    COMPARATIVE = "COMPARATIVE"
    NUMERIC = "NUMERIC"
    TEMPORAL = "TEMPORAL"
    CAUSAL = "CAUSAL"
    PERFORMANCE = "PERFORMANCE"
    LIMITATION = "LIMITATION"
    REQUIREMENT = "REQUIREMENT"
    OTHER = "OTHER"


class ClaimStatus(StrEnum):
    CORROBORATED = "CORROBORATED"
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONTESTED = "CONTESTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ConfidenceTier(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNRESOLVED = "UNRESOLVED"


class RelationLabel(StrEnum):
    ENTAILMENT = "ENTAILMENT"
    NEUTRAL = "NEUTRAL"
    CONTRADICTION = "CONTRADICTION"


class DecisionLabel(StrEnum):
    DIRECT_SUPPORT = "DIRECT_SUPPORT"
    PARTIAL_SUPPORT = "PARTIAL_SUPPORT"
    CONTRADICTION = "CONTRADICTION"
    NEUTRAL = "NEUTRAL"
    UNCLEAR = "UNCLEAR"


class EvidenceRequest(BaseModel):
    agent_run_id: str | None = None
    max_subquestions: int = Field(default=8, ge=1, le=30)
    max_claims_per_subquestion: int = Field(default=20, ge=1, le=40)
    evidence_candidates_per_claim: int = Field(default=20, ge=1, le=50)
    final_evidence_per_claim: int = Field(default=8, ge=1, le=30)
    use_nli: bool = True
    use_llm_adjudication: bool = True
    adjudicate_all: bool = False
    use_counterqueries: bool = True
    max_claim_extraction_chunks: int = Field(default=8, ge=1, le=20)
    max_chars_per_chunk: int = Field(default=2500, ge=100, le=5000)
    max_claim_extraction_chars: int = Field(default=16000, ge=500, le=30000)

    @model_validator(mode="after")
    def valid_limits(self):
        if self.final_evidence_per_claim > self.evidence_candidates_per_claim:
            raise ValueError("final_evidence_per_claim exceeds evidence_candidates_per_claim")
        return self


class ClaimCandidate(BaseModel):
    text: str = Field(min_length=5, max_length=1500)
    normalized_text: str | None = None
    claim_type: ClaimType = ClaimType.OTHER
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    qualifiers: list[str] = Field(default_factory=list, max_length=12)
    temporal_scope: str | None = None
    conditions: list[str] = Field(default_factory=list, max_length=12)
    originating_chunk_ids: list[int] = Field(min_length=1, max_length=12)


class ClaimExtractionResult(BaseModel):
    claims: list[ClaimCandidate] = Field(max_length=40)


class ClaimEquivalenceResult(BaseModel):
    same_claim: bool
    reason_summary: str = Field(max_length=400)


class CounterEvidenceQueryPlan(BaseModel):
    queries: list[str] = Field(max_length=2)


class EvidenceRelationResult(BaseModel):
    relation: RelationLabel
    entailment_score: float = Field(ge=0, le=1)
    neutral_score: float = Field(ge=0, le=1)
    contradiction_score: float = Field(ge=0, le=1)
    model_name: str


class AdjudicationResult(BaseModel):
    relation: DecisionLabel
    decision_summary: str = Field(max_length=500)
    important_qualifier_mismatch: bool = False


class ClaimListFilters(BaseModel):
    subquestion_id: str | None = None
    status: ClaimStatus | None = None
    confidence: ConfidenceTier | None = None
    has_contradiction: bool | None = None
    source_domain: str | None = None
    order_by: str = "created_at"
