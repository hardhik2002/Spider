"""Phase 5 agent lifecycle and inspectable research trace."""

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.agent.models import AgentAction, AgentIteration, AgentRun, EvidenceAssessment, ResearchGap
from app.agent.schemas import AgentRequest
from app.agent.service import BudgetManager
from app.db.models import ResearchSubquestion

router = APIRouter(prefix="/api/v1/research", tags=["agent"])


async def _get(request: Request, job_id: UUID, run_id: UUID) -> AgentRun:
    async with request.app.state.agent_service.sessions() as session:
        row = await session.get(AgentRun, str(run_id))
        if row is None or row.research_job_id != str(job_id):
            raise HTTPException(404, "Agent run not found")
        return row


@router.post("/{job_id}/agent", status_code=status.HTTP_202_ACCEPTED)
async def start_agent(job_id: UUID, payload: AgentRequest, request: Request) -> dict:
    try:
        row = await request.app.state.agent_service.start(str(job_id), payload)
    except KeyError as exc:
        raise HTTPException(404, "Research job not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "agent_run_id": row.id,
        "research_job_id": row.research_job_id,
        "status": row.status.lower(),
    }


@router.get("/{job_id}/agent/{run_id}")
async def agent_status(job_id: UUID, run_id: UUID, request: Request) -> dict:
    row = await _get(request, job_id, run_id)
    service = request.app.state.agent_service
    async with service.sessions() as session:
        total = len(
            (
                await session.execute(
                    select(ResearchSubquestion.id).where(
                        ResearchSubquestion.research_job_id == str(job_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assessments = (
            (
                await session.execute(
                    select(EvidenceAssessment).where(
                        EvidenceAssessment.agent_run_id == row.id,
                        EvidenceAssessment.iteration_number == row.current_iteration,
                    )
                )
            )
            .scalars()
            .all()
        )
        gaps = (
            (
                await session.execute(
                    select(ResearchGap).where(
                        ResearchGap.agent_run_id == row.id,
                        ResearchGap.status.in_(["OPEN", "IN_PROGRESS"]),
                    )
                )
            )
            .scalars()
            .all()
        )
    sufficient = sum(
        a.coverage == "sufficient" and a.deterministic_minimum_met and not a.needs_more_research
        for a in assessments
    )
    budget = BudgetManager(AgentRequest.model_validate_json(row.request_json), row).remaining()
    return {
        "agent_run_id": row.id,
        "research_job_id": row.research_job_id,
        "status": row.status,
        "current_iteration": row.current_iteration,
        "current_node": row.current_node,
        "stop_reason": row.stop_reason,
        "subquestions_total": total,
        "subquestions_sufficient": sufficient,
        "subquestions_with_gaps": len({g.subquestion_id for g in gaps}),
        "new_queries_generated": row.queries_used,
        "new_searches_executed": row.searches_used,
        "new_seeds_selected": row.seeds_used,
        "new_pages_crawled": row.pages_crawled,
        "new_page_attempts": row.pages_used,
        "new_documents_indexed": row.documents_indexed,
        "iterations_used": row.current_iteration,
        "remaining_budgets": budget,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "duration_ms": row.duration_ms,
        "error_message": row.error_message,
    }


@router.get("/{job_id}/agent/{run_id}/iterations")
async def agent_iterations(job_id: UUID, run_id: UUID, request: Request) -> dict:
    await _get(request, job_id, run_id)
    async with request.app.state.agent_service.sessions() as session:
        rows = (
            (
                await session.execute(
                    select(AgentIteration)
                    .where(AgentIteration.agent_run_id == str(run_id))
                    .order_by(AgentIteration.number)
                )
            )
            .scalars()
            .all()
        )
    return {
        "agent_run_id": str(run_id),
        "iterations": [
            {
                "iteration": row.number,
                "status": row.status,
                "selected_gap_id": row.selected_gap_id,
                "action": row.action,
                "started_at": row.started_at,
                "completed_at": row.completed_at,
                "trace": json.loads(row.trace_json),
            }
            for row in rows
        ],
    }


@router.get("/{job_id}/agent/{run_id}/gaps")
async def agent_gaps(job_id: UUID, run_id: UUID, request: Request) -> dict:
    await _get(request, job_id, run_id)
    async with request.app.state.agent_service.sessions() as session:
        rows = (
            await session.execute(
                select(ResearchGap, ResearchSubquestion)
                .join(ResearchSubquestion, ResearchSubquestion.id == ResearchGap.subquestion_id)
                .where(ResearchGap.agent_run_id == str(run_id))
                .order_by(ResearchGap.created_at)
            )
        ).all()
        actions = (
            (
                await session.execute(
                    select(AgentAction).where(AgentAction.agent_run_id == str(run_id))
                )
            )
            .scalars()
            .all()
        )
    by_gap = {}
    for action in actions:
        by_gap.setdefault(action.gap_id, []).append(
            {"kind": action.kind, "status": action.status, "data": json.loads(action.data_json)}
        )
    return {
        "agent_run_id": str(run_id),
        "gaps": [
            {
                "id": gap.id,
                "subquestion_id": sub.plan_id,
                "subquestion": sub.question,
                "description": gap.description,
                "gap_type": gap.gap_type,
                "priority": gap.priority,
                "status": gap.status,
                "evidence_count": gap.evidence_count,
                "iteration_created": gap.iteration_created,
                "actions": by_gap.get(gap.id, []),
                "derived_search_queries": [
                    a["data"]["query"] for a in by_gap.get(gap.id, []) if a["kind"] == "SEARCH"
                ],
                "resolved_at": gap.resolved_at,
                "resolution_iteration": gap.resolution_iteration,
            }
            for gap, sub in rows
        ],
    }


@router.post("/{job_id}/agent/{run_id}/cancel")
async def cancel_agent(job_id: UUID, run_id: UUID, request: Request) -> dict:
    await _get(request, job_id, run_id)
    row = await request.app.state.agent_service.cancel(str(run_id))
    return {"agent_run_id": row.id, "status": row.status, "cancel_requested": row.cancel_requested}
