"""Phase 6 evidence ledger and inspectable verification endpoints."""

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import case, select

from app.db.models import ResearchSubquestion
from app.evidence.models import (
    ClaimCitation,
    ClaimEvidenceRelation,
    ClaimOrigin,
    EvidenceJob,
    VerifiedClaim,
)
from app.evidence.schemas import ClaimListFilters, ClaimStatus, ConfidenceTier, EvidenceRequest

router = APIRouter(prefix="/api/v1/research", tags=["evidence"])


async def _job(request: Request, research_job_id: UUID, job_id: UUID) -> EvidenceJob:
    async with request.app.state.evidence_service.sessions() as session:
        row = await session.get(EvidenceJob, str(job_id))
        if row is None or row.research_job_id != str(research_job_id):
            raise HTTPException(404, "Evidence job not found")
        return row


@router.post("/{research_job_id}/evidence", status_code=status.HTTP_202_ACCEPTED)
async def start_evidence(
    research_job_id: UUID, payload: EvidenceRequest, request: Request
) -> dict:
    try:
        row = await request.app.state.evidence_service.start(str(research_job_id), payload)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"evidence_job_id": row.id, "status": row.status.lower()}


@router.get("/{research_job_id}/evidence/{job_id}")
async def evidence_status(research_job_id: UUID, job_id: UUID, request: Request) -> dict:
    row = await _job(request, research_job_id, job_id)
    counters = json.loads(row.counters_json)
    return {
        "evidence_job_id": row.id,
        "research_job_id": row.research_job_id,
        "agent_run_id": row.agent_run_id,
        "status": row.status,
        "stage": row.stage,
        **{name: counters.get(name, 0) for name in (
            "subquestions_processed", "claims_extracted", "claims_normalized",
            "claims_deduplicated", "direct_support_relations", "contradiction_relations",
            "neutral_relations", "claims_corroborated", "claims_supported",
            "claims_contested", "claims_contradicted", "claims_partially_supported",
            "claims_insufficient_evidence", "citations_created", "citations_validated",
            "citation_failures", "nli_pairs", "llm_extraction_calls",
            "llm_adjudication_calls",
        )},
        "timings": json.loads(row.timings_json),
        "duration_ms": row.duration_ms,
        "error": row.error_message,
    }


def _claim_summary(row: VerifiedClaim, subquestion: str | None = None) -> dict:
    return {
        "id": row.id,
        "stable_key": row.stable_key,
        "subquestion_id": row.subquestion_id,
        "subquestion": subquestion,
        "claim": row.claim_text,
        "normalized_claim": row.normalized_claim,
        "claim_type": row.claim_type,
        "status": row.status,
        "confidence_tier": row.confidence_tier,
        "support_count": row.support_count,
        "contradiction_count": row.contradiction_count,
        "partial_count": row.partial_count,
        "independent_support_groups": row.independent_support_groups,
        "independent_contradiction_groups": row.independent_contradiction_groups,
        "needs_more_verification": row.needs_more_verification,
        "created_at": row.created_at,
    }


@router.get("/{research_job_id}/evidence/{job_id}/claims")
async def list_claims(
    research_job_id: UUID,
    job_id: UUID,
    request: Request,
    subquestion_id: str | None = None,
    status: ClaimStatus | None = None,
    confidence: ConfidenceTier | None = None,
    has_contradiction: bool | None = None,
    source_domain: str | None = None,
    order_by: str = "created_at",
) -> dict:
    await _job(request, research_job_id, job_id)
    filters = ClaimListFilters(
        subquestion_id=subquestion_id,
        status=status,
        confidence=confidence,
        has_contradiction=has_contradiction,
        source_domain=source_domain,
        order_by=order_by,
    )
    async with request.app.state.evidence_service.sessions() as session:
        query = select(VerifiedClaim, ResearchSubquestion).join(
            ResearchSubquestion, ResearchSubquestion.id == VerifiedClaim.subquestion_id
        ).where(VerifiedClaim.evidence_job_id == str(job_id))
        if filters.subquestion_id:
            query = query.where(ResearchSubquestion.plan_id == filters.subquestion_id)
        if filters.status:
            query = query.where(VerifiedClaim.status == filters.status)
        if filters.confidence:
            query = query.where(VerifiedClaim.confidence_tier == filters.confidence)
        if filters.has_contradiction is not None:
            predicate = VerifiedClaim.contradiction_count > 0
            query = query.where(predicate if filters.has_contradiction else ~predicate)
        if filters.source_domain:
            query = query.where(
                VerifiedClaim.id.in_(
                    select(ClaimEvidenceRelation.claim_id).where(
                        ClaimEvidenceRelation.source_domain == filters.source_domain.lower()
                    )
                )
            )
        ordering = {
            "confidence": case(
                (VerifiedClaim.confidence_tier == "HIGH", 0),
                (VerifiedClaim.confidence_tier == "MEDIUM", 1),
                (VerifiedClaim.confidence_tier == "LOW", 2),
                else_=3,
            ),
            "support_count": VerifiedClaim.support_count.desc(),
            "contradiction_count": VerifiedClaim.contradiction_count.desc(),
            "created_at": VerifiedClaim.created_at,
        }
        if filters.order_by not in ordering:
            raise HTTPException(422, "Unsupported order_by")
        rows = (await session.execute(query.order_by(ordering[filters.order_by], VerifiedClaim.id))).all()
    return {"evidence_job_id": str(job_id), "claims": [
        _claim_summary(claim, sub.question) for claim, sub in rows
    ]}


async def _detail(service, job_id: str, claim_id: str) -> dict:
    async with service.sessions() as session:
        claim = await session.get(VerifiedClaim, claim_id)
        if claim is None or claim.evidence_job_id != job_id:
            raise HTTPException(404, "Claim not found")
        sub = await session.get(ResearchSubquestion, claim.subquestion_id)
        relations = (
            await session.execute(
                select(ClaimEvidenceRelation)
                .where(ClaimEvidenceRelation.claim_id == claim_id)
                .order_by(ClaimEvidenceRelation.retrieval_rank, ClaimEvidenceRelation.id)
            )
        ).scalars().all()
        citations = (
            await session.execute(select(ClaimCitation).where(ClaimCitation.claim_id == claim_id))
        ).scalars().all()
        origins = (
            await session.execute(select(ClaimOrigin).where(ClaimOrigin.claim_id == claim_id))
        ).scalars().all()
    citation_items = [
        {
            "id": row.id, "relation_id": row.relation_id,
            "chunk_id": row.chunk_id, "document_id": row.document_id,
            "source_url": row.source_url, "start_offset": row.start_offset,
            "end_offset": row.end_offset, "exact_text": row.exact_text,
            "relation": row.relation,
        }
        for row in citations
    ]
    relation_items = [
        {
            "chunk_id": row.chunk_id, "document_id": row.document_id,
            "source_group_id": row.source_group_id,
            "source_url": row.source_url, "source_domain": row.source_domain,
            "retrieval_rank": row.retrieval_rank, "retrieval_score": row.retrieval_score,
            "nli_relation": row.nli_relation,
            "nli_scores": {
                "entailment": row.entailment_score,
                "neutral": row.neutral_score,
                "contradiction": row.contradiction_score,
            },
            "nli_model_name": row.nli_model_name,
            "relation": row.adjudicated_relation,
            "qualifier_mismatch": row.qualifier_mismatch,
            "decision_summary": row.adjudication_summary,
        }
        for row in relations
    ]
    support = [r for r in relation_items if r["relation"] == "DIRECT_SUPPORT"]
    contradict = [r for r in relation_items if r["relation"] == "CONTRADICTION"]
    neutral = [r for r in relation_items if r["relation"] in {"NEUTRAL", "UNCLEAR"}]
    return {
        **_claim_summary(claim, sub.question),
        "qualifiers": json.loads(claim.qualifiers_json),
        "conditions": json.loads(claim.conditions_json),
        "temporal_scope": claim.temporal_scope,
        "subject": claim.subject,
        "predicate": claim.predicate,
        "object": claim.object,
        "originating_chunks": [
            {"chunk_id": row.chunk_id, "document_id": row.document_id, "source_url": row.source_url}
            for row in origins
        ],
        "supporting_evidence": support,
        "contradicting_evidence": contradict,
        "neutral_evidence": neutral,
        "partial_evidence": [r for r in relation_items if r["relation"] == "PARTIAL_SUPPORT"],
        "citations": citation_items,
        "source_diversity": {
            "supporting_unique_documents": len({r["document_id"] for r in support}),
            "supporting_unique_domains": len({r["source_domain"] for r in support}),
            "supporting_source_groups": len({r["source_group_id"] for r in support}),
            "contradicting_unique_documents": len({r["document_id"] for r in contradict}),
            "contradicting_unique_domains": len({r["source_domain"] for r in contradict}),
            "contradicting_source_groups": len({r["source_group_id"] for r in contradict}),
        },
        "decision_metadata": json.loads(claim.decision_metadata_json),
        "verification_gap_reason": claim.verification_gap_reason,
    }


@router.get("/{research_job_id}/evidence/{job_id}/claims/{claim_id}")
async def claim_detail(research_job_id: UUID, job_id: UUID, claim_id: str, request: Request) -> dict:
    await _job(request, research_job_id, job_id)
    return await _detail(request.app.state.evidence_service, str(job_id), claim_id)


@router.get("/{research_job_id}/evidence/{job_id}/contradictions")
async def contradictions(research_job_id: UUID, job_id: UUID, request: Request) -> dict:
    await _job(request, research_job_id, job_id)
    async with request.app.state.evidence_service.sessions() as session:
        ids = (
            await session.execute(
                select(VerifiedClaim.id).where(
                    VerifiedClaim.evidence_job_id == str(job_id),
                    VerifiedClaim.contradiction_count > 0,
                )
            )
        ).scalars().all()
    return {"claims": [
        await _detail(request.app.state.evidence_service, str(job_id), claim_id)
        for claim_id in ids
    ]}


@router.get("/{research_job_id}/evidence/{job_id}/ledger")
async def ledger(research_job_id: UUID, job_id: UUID, request: Request) -> dict:
    await _job(request, research_job_id, job_id)
    async with request.app.state.evidence_service.sessions() as session:
        claims = (
            await session.execute(
                select(VerifiedClaim)
                .where(VerifiedClaim.evidence_job_id == str(job_id))
                .order_by(VerifiedClaim.created_at, VerifiedClaim.id)
            )
        ).scalars().all()
    items = []
    for claim in claims:
        detail = await _detail(request.app.state.evidence_service, str(job_id), claim.id)
        items.append({
            "claim_id": claim.id,
            "claim": claim.claim_text,
            "status": claim.status,
            "confidence": claim.confidence_tier,
            "supporting_sources": claim.independent_support_groups,
            "contradicting_sources": claim.independent_contradiction_groups,
            "needs_more_verification": claim.needs_more_verification,
            "evidence_mapping": detail["supporting_evidence"] + detail["contradicting_evidence"],
            "citations": detail["citations"],
            "verification_metadata": detail["decision_metadata"],
        })
    return {"evidence_job_id": str(job_id), "claims": items}
