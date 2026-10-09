"""Bounded, checkpointed Phase 5 research workflow built from Phase 2-4 services."""

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import UTC
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, or_, select

from app.agent.llm import EvidenceAssessor, GapQueryGenerator
from app.agent.models import AgentAction, AgentIteration, AgentRun, EvidenceAssessment, ResearchGap
from app.agent.schemas import AgentRequest, AssessmentResult
from app.crawler.normalizer import hostname, normalize_url
from app.crawler.scoring import cosine_similarity
from app.db.models import (
    CrawledPage,
    CrawlJob,
    ResearchJob,
    ResearchQuerySubquestion,
    ResearchResultOccurrence,
    ResearchSearchQuery,
    ResearchSearchResult,
    ResearchSeed,
    ResearchSubquestion,
    utc_now,
)
from app.rag.consistency import check_consistency
from app.rag.models import IndexJob
from app.schemas.crawl import CrawlRequest
from app.schemas.rag import RetrievalRequest
from app.services.research_service import result_representation, seed_score

logger = logging.getLogger("spidermind.agent")
TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "BUDGET_EXHAUSTED"}
PRIORITY = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def gap_priority(parent_priority: str, gap_type: str) -> str:
    """Combine the plan's priority with the missing evidence's importance."""
    base = {"high": 3, "medium": 2, "low": 1}.get(parent_priority.lower(), 1)
    importance = (
        1
        if gap_type in {"missing_primary_evidence", "missing_metric", "missing_recent_evidence"}
        else 0
    )
    score = base + importance
    return "HIGH" if score >= 3 else "MEDIUM" if score == 2 else "LOW"


def query_key(value: str) -> str:
    return " ".join(value.casefold().split())


def elapsed_ms(started, completed) -> int:
    """SQLite returns naive datetimes even for timezone-aware columns."""
    return int((completed.replace(tzinfo=UTC) - started.replace(tzinfo=UTC)).total_seconds() * 1000)


class ResearchAgentState(TypedDict, total=False):
    research_job_id: str
    agent_run_id: str
    iteration: int
    subquestion_ids: list[int]
    evidence_snapshot: dict[str, dict]
    unresolved_gap_ids: list[str]
    selected_gap_id: str | None
    pending_queries: list[str]
    pending_seed_ids: list[int]
    new_crawl_job_ids: list[str]
    new_index_job_ids: list[str]
    action: str | None
    stop_reason: str | None
    budget: dict
    metrics: dict
    stagnant: int
    prior_signature: dict[str, list[int]]
    prior_source_signature: dict[str, list[int]]
    prior_coverage: dict[str, str]


class BudgetManager:
    """Central hard limits. A sequential graph owns one run's reservations."""

    def __init__(self, request: AgentRequest, run: AgentRun) -> None:
        self.request = request
        self.run = run

    def remaining(self) -> dict[str, int]:
        r, q = self.run, self.request
        return {
            "iterations": max(0, q.max_iterations - r.current_iteration),
            "queries": max(0, q.max_new_search_queries - r.queries_used),
            "seeds": max(0, q.max_new_seeds - r.seeds_used),
            "pages": max(0, q.max_new_pages - r.pages_used),
            "runtime_seconds": max(
                0,
                q.max_runtime_seconds
                - int((utc_now() - r.started_at.replace(tzinfo=UTC)).total_seconds()),
            )
            if r.started_at
            else q.max_runtime_seconds,
        }

    def stop_reason(self) -> str | None:
        if self.run.cancel_requested:
            return "CANCELLED"
        remaining = self.remaining()
        for key, reason in (
            ("runtime_seconds", "TIME_BUDGET"),
            ("iterations", "MAX_ITERATIONS"),
            ("queries", "QUERY_BUDGET"),
            ("seeds", "SEED_BUDGET"),
            ("pages", "PAGE_BUDGET"),
        ):
            if remaining[key] <= 0:
                return reason
        return None


class AgentService:
    def __init__(
        self,
        sessions,
        settings,
        research_service,
        crawl_service,
        index_service,
        retrieval_service,
        assessor: EvidenceAssessor,
        query_generator: GapQueryGenerator,
        checkpointer,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.research = research_service
        self.crawl = crawl_service
        self.index = index_service
        self.retrieval = retrieval_service
        self.assessor = assessor
        self.query_generator = query_generator
        self.tasks: set[asyncio.Task] = set()
        self._monotonic_started: dict[str, float] = {}
        graph = StateGraph(ResearchAgentState)
        for name, node in (
            ("initialize", self.initialize),
            ("retrieve", self.retrieve),
            ("assess", self.assess),
            ("update_gaps", self.update_gaps),
            ("check_stop", self.check_stop),
            ("select_gap", self.select_gap),
            ("generate_queries", self.generate_queries),
            ("search", self.search),
            ("select_seeds", self.select_seeds),
            ("crawl", self.crawl_seeds),
            ("index", self.index_pages),
            ("advance", self.advance),
            ("finalize", self.finalize),
        ):
            graph.add_node(name, self._instrument(name, node))
        graph.add_edge(START, "initialize")
        for a, b in (
            ("initialize", "retrieve"),
            ("update_gaps", "check_stop"),
            ("advance", "retrieve"),
            ("finalize", END),
        ):
            graph.add_edge(a, b)
        graph.add_conditional_edges(
            "check_stop",
            lambda s: "finalize" if s.get("stop_reason") else "select_gap",
            {"finalize": "finalize", "select_gap": "select_gap"},
        )
        graph.add_conditional_edges(
            "select_gap",
            lambda s: "finalize" if s.get("stop_reason") else "generate_queries",
            {"finalize": "finalize", "generate_queries": "generate_queries"},
        )
        for name, next_node in (
            ("retrieve", "assess"),
            ("assess", "update_gaps"),
            ("generate_queries", "search"),
            ("search", "select_seeds"),
            ("select_seeds", "crawl"),
            ("crawl", "index"),
            ("index", "advance"),
        ):
            graph.add_conditional_edges(
                name,
                lambda s, target=next_node: "finalize" if s.get("stop_reason") else target,
                {"finalize": "finalize", next_node: next_node},
            )
        self.graph = graph.compile(checkpointer=checkpointer)

    def _instrument(self, name, node):
        async def invoke(state: ResearchAgentState):
            started = time.monotonic()
            run_id = state["agent_run_id"]
            before = await self._budget_snapshot(run_id)
            context = {
                "agent_run_id": run_id,
                "research_job_id": state["research_job_id"],
                "iteration": state["iteration"],
                "gap_id": state.get("selected_gap_id"),
            }
            logger.info("agent_%s_started", name, extra=context)
            try:
                result = await node(state)
            except Exception:
                logger.exception("agent_%s_failed", name, extra=context)
                raise
            duration = int((time.monotonic() - started) * 1000)
            after = await self._budget_snapshot(run_id)
            await self._trace(
                {**state, **result},
                "node_timing",
                operation=name,
                duration_ms=duration,
                budget_before=before,
                budget_after=after,
            )
            logger.info("agent_%s_completed", name, extra={**context, "duration_ms": duration})
            return result

        return invoke

    async def _budget_snapshot(self, run_id: str) -> dict:
        run = await self._run_row(run_id)
        return BudgetManager(AgentRequest.model_validate_json(run.request_json), run).remaining()

    async def _llm_counter(self, run_id: str, *, failure: bool = False) -> None:
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if failure:
                run.llm_failures += 1
            else:
                run.llm_calls += 1
            await session.commit()

    async def _run_row(self, run_id: str) -> AgentRun:
        async with self.sessions() as session:
            row = await session.get(AgentRun, run_id)
            if row is None:
                raise KeyError(run_id)
            return row

    async def _request(self, run_id: str) -> AgentRequest:
        return AgentRequest.model_validate_json((await self._run_row(run_id)).request_json)

    async def _time_left(self, run_id: str) -> float:
        run = await self._run_row(run_id)
        request = AgentRequest.model_validate_json(run.request_json)
        if run.started_at is None:
            return float(request.max_runtime_seconds)
        wall_elapsed = (utc_now() - run.started_at.replace(tzinfo=UTC)).total_seconds()
        local_started = self._monotonic_started.get(run_id)
        local_elapsed = time.monotonic() - local_started if local_started is not None else 0
        return request.max_runtime_seconds - max(wall_elapsed, local_elapsed)

    async def _within_time(self, run_id: str, awaitable):
        remaining = await self._time_left(run_id)
        if remaining <= 0:
            raise TimeoutError("Agent runtime budget exhausted")
        return await asyncio.wait_for(awaitable, timeout=remaining)

    async def _guard(self, state: ResearchAgentState, node: str) -> str | None:
        run = await self._run_row(state["agent_run_id"])
        request = AgentRequest.model_validate_json(run.request_json)
        remaining = BudgetManager(request, run).remaining()
        reason = (
            "CANCELLED"
            if run.cancel_requested
            else "TIME_BUDGET"
            if remaining["runtime_seconds"] <= 0
            else None
        )
        async with self.sessions() as session:
            row = await session.get(AgentRun, run.id)
            row.current_node = node
            if row.status not in TERMINAL:
                row.status = (
                    node.upper()
                    if node.upper()
                    in {
                        "INITIALIZING",
                        "RETRIEVING",
                        "ASSESSING",
                        "SEARCHING",
                        "CRAWLING",
                        "INDEXING",
                    }
                    else "RUNNING"
                )
            await session.commit()
        return reason

    async def _trace(self, state: ResearchAgentState, node: str, **data) -> None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(AgentIteration).where(
                        AgentIteration.agent_run_id == state["agent_run_id"],
                        AgentIteration.number == state["iteration"],
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = AgentIteration(agent_run_id=state["agent_run_id"], number=state["iteration"])
                session.add(row)
            trace = json.loads(row.trace_json or "[]")
            trace.append({"node": node, "at": utc_now().isoformat(), **data})
            row.trace_json = json.dumps(trace)
            await session.commit()

    async def start(self, research_job_id: str, request: AgentRequest) -> AgentRun:
        defaults = {
            "max_iterations": self.settings.agent_max_iterations,
            "max_new_search_queries": self.settings.agent_max_queries,
            "max_new_seeds": self.settings.agent_max_seeds,
            "max_new_pages": self.settings.agent_max_pages,
            "max_runtime_seconds": self.settings.agent_max_runtime_seconds,
            "min_evidence_chunks_per_subquestion": self.settings.agent_min_evidence_chunks,
            "min_unique_sources_per_subquestion": self.settings.agent_min_unique_sources,
            "retrieval_top_k": self.settings.agent_retrieval_top_k,
            "rerank": self.settings.agent_rerank_enabled,
            "max_queries_per_gap": self.settings.agent_max_queries_per_gap,
            "max_assessment_chunks": self.settings.agent_max_assessment_chunks,
            "max_assessment_chars_per_chunk": self.settings.agent_max_assessment_chars_per_chunk,
            "max_total_assessment_chars": self.settings.agent_max_total_assessment_chars,
        }
        effective = request.model_dump()
        for field, value in defaults.items():
            if field not in request.model_fields_set:
                effective[field] = value
        request = AgentRequest.model_validate(effective)
        async with self.sessions() as session:
            job = await session.get(ResearchJob, research_job_id)
            if job is None:
                raise KeyError(research_job_id)
            if job.status not in {"completed", "partial"}:
                raise ValueError("Research plan and initial crawl must finish before agent start")
            existing_pages = (
                await session.scalar(
                    select(func.count())
                    .select_from(CrawledPage)
                    .join(ResearchSeed, ResearchSeed.crawl_job_id == CrawledPage.crawl_job_id)
                    .where(
                        ResearchSeed.research_job_id == research_job_id,
                        ResearchSeed.selected.is_(True),
                        CrawledPage.crawl_status == "COMPLETED",
                        CrawledPage.duplicate_of_page_id.is_(None),
                    )
                )
            ) or 0
            if existing_pages:
                indexed = (
                    await session.execute(
                        select(IndexJob.id)
                        .where(
                            IndexJob.research_job_id == research_job_id,
                            IndexJob.status.in_(["COMPLETED", "PARTIAL"]),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if indexed is None:
                    raise ValueError("Index existing research pages before starting the agent")
            active = (
                await session.execute(
                    select(AgentRun).where(
                        AgentRun.research_job_id == research_job_id, ~AgentRun.status.in_(TERMINAL)
                    )
                )
            ).scalar_one_or_none()
            if active:
                return active
            row = AgentRun(research_job_id=research_job_id, request_json=request.model_dump_json())
            session.add(row)
            await session.commit()
        self._launch(row.id)
        return row

    def _launch(self, run_id: str) -> None:
        task = asyncio.create_task(self.run(run_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def recover_jobs(self) -> None:
        async with self.sessions() as session:
            rows = (
                (await session.execute(select(AgentRun).where(~AgentRun.status.in_(TERMINAL))))
                .scalars()
                .all()
            )
        for row in rows:
            self._launch(row.id)

    async def run(self, run_id: str) -> None:
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 200}
        try:
            existing = await self._run_row(run_id)
            elapsed = (
                (utc_now() - existing.started_at.replace(tzinfo=UTC)).total_seconds()
                if existing.started_at
                else 0
            )
            self._monotonic_started[run_id] = time.monotonic() - max(0, elapsed)
            snapshot = await self.graph.aget_state(config)
            if snapshot.values and snapshot.next:
                await self.graph.ainvoke(None, config)
            elif snapshot.values and not snapshot.next:
                # A graph may have finished just before its domain status commit.
                await self.finalize(snapshot.values)
            else:
                row = await self._run_row(run_id)
                await self.graph.ainvoke(
                    {
                        "research_job_id": row.research_job_id,
                        "agent_run_id": run_id,
                        "iteration": 0,
                        "stagnant": 0,
                        "prior_signature": {},
                    },
                    config,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("agent run %s failed", run_id)
            async with self.sessions() as session:
                row = await session.get(AgentRun, run_id)
                if row and row.status not in TERMINAL:
                    row.status = (
                        "PARTIAL" if row.current_iteration or row.documents_indexed else "FAILED"
                    )
                    row.stop_reason = "ERROR"
                    row.error_message = f"{type(exc).__name__}: {exc}"[:2000]
                    row.completed_at = utc_now()
                    if row.started_at:
                        row.duration_ms = elapsed_ms(row.started_at, row.completed_at)
                    await session.commit()
        finally:
            self._monotonic_started.pop(run_id, None)

    async def initialize(self, state: ResearchAgentState) -> dict:
        async with self.sessions() as session:
            row = await session.get(AgentRun, state["agent_run_id"])
            if row.started_at is None:
                row.started_at = utc_now()
            subs = (
                (
                    await session.execute(
                        select(ResearchSubquestion)
                        .where(ResearchSubquestion.research_job_id == row.research_job_id)
                        .order_by(ResearchSubquestion.order)
                    )
                )
                .scalars()
                .all()
            )
            if not subs:
                raise ValueError("Research job has no planned subquestions")
            await session.commit()
        await self._trace(state, "initialize", subquestions=len(subs))
        return {"subquestion_ids": [s.id for s in subs], "budget": {}, "metrics": {}}

    async def retrieve(self, state: ResearchAgentState) -> dict:
        reason = await self._guard(state, "RETRIEVING")
        if reason:
            return {"stop_reason": reason}
        request = await self._request(state["agent_run_id"])
        snapshot = {}
        async with self.sessions() as session:
            subs = (
                (
                    await session.execute(
                        select(ResearchSubquestion).where(
                            ResearchSubquestion.id.in_(state["subquestion_ids"])
                        )
                    )
                )
                .scalars()
                .all()
            )
        for sub in subs:
            try:
                result = await self._within_time(
                    state["agent_run_id"],
                    self.retrieval.retrieve(
                        state["research_job_id"],
                        RetrievalRequest(
                            query=sub.question,
                            retrieval_mode="hybrid",
                            final_top_k=request.retrieval_top_k,
                            rerank=request.rerank,
                            include_neighbor_context=False,
                            subquestion_id=sub.plan_id,
                        ),
                    ),
                )
                rows = result["results"]
                snapshot[str(sub.id)] = {
                    "chunk_ids": list(dict.fromkeys(r["chunk_id"] for r in rows)),
                    "document_ids": list(dict.fromkeys(r["document_id"] for r in rows)),
                    "domains": list(dict.fromkeys(r["source_domain"] for r in rows)),
                    "timings": result.get("timings", {}),
                    "error": None,
                }
            except TimeoutError:
                return {"stop_reason": "TIME_BUDGET", "evidence_snapshot": snapshot}
            except Exception as exc:
                logger.warning("retrieval failed for subquestion %s: %s", sub.id, exc)
                snapshot[str(sub.id)] = {
                    "chunk_ids": [],
                    "document_ids": [],
                    "domains": [],
                    "timings": {},
                    "error": str(exc)[:300],
                }
        await self._trace(
            state, "retrieve", evidence_counts={k: len(v["chunk_ids"]) for k, v in snapshot.items()}
        )
        return {"evidence_snapshot": snapshot}

    async def assess(self, state: ResearchAgentState) -> dict:
        reason = await self._guard(state, "ASSESSING")
        if reason:
            return {"stop_reason": reason}
        request = await self._request(state["agent_run_id"])
        async with self.sessions() as session:
            subs = (
                (
                    await session.execute(
                        select(ResearchSubquestion).where(
                            ResearchSubquestion.id.in_(state["subquestion_ids"])
                        )
                    )
                )
                .scalars()
                .all()
            )
        for sub in subs:
            async with self.sessions() as session:
                existing = (
                    await session.execute(
                        select(EvidenceAssessment).where(
                            EvidenceAssessment.agent_run_id == state["agent_run_id"],
                            EvidenceAssessment.iteration_number == state["iteration"],
                            EvidenceAssessment.subquestion_id == sub.id,
                        )
                    )
                ).scalar_one_or_none()
            if existing:
                continue
            snap = state["evidence_snapshot"].get(str(sub.id), {})
            minimum = (
                len(snap.get("chunk_ids", [])) >= request.min_evidence_chunks_per_subquestion
                and len(snap.get("document_ids", [])) >= request.min_unique_sources_per_subquestion
            )
            llm_called = False
            try:
                result = await self._within_time(
                    state["agent_run_id"],
                    self.retrieval.retrieve(
                        state["research_job_id"],
                        RetrievalRequest(
                            query=sub.question,
                            retrieval_mode="hybrid",
                            final_top_k=request.retrieval_top_k,
                            rerank=False,
                            include_neighbor_context=False,
                            subquestion_id=sub.plan_id,
                        ),
                    ),
                )
                await self._llm_counter(state["agent_run_id"])
                llm_called = True
                assessed = await self._within_time(
                    state["agent_run_id"],
                    self.assessor.assess(
                        {
                            "plan_id": sub.plan_id,
                            "question": sub.question,
                            "expected_evidence": sub.expected_evidence,
                        },
                        result["results"],
                        request.model_dump(),
                    ),
                )
                if assessed.subquestion_id != sub.plan_id:
                    raise ValueError("Assessor changed subquestion ID")
            except TimeoutError:
                if llm_called:
                    await self._llm_counter(state["agent_run_id"], failure=True)
                return {"stop_reason": "TIME_BUDGET"}
            except Exception as exc:
                if llm_called:
                    await self._llm_counter(state["agent_run_id"], failure=True)
                logger.warning("assessment failed for subquestion %s: %s", sub.id, exc)
                assessed = AssessmentResult(
                    subquestion_id=sub.plan_id,
                    coverage="insufficient",
                    evidence_summary=f"Assessment unavailable: {type(exc).__name__}",
                    missing_aspects=["Evidence coverage could not be established"],
                    needs_more_research=True,
                    suggested_gap_types=["other"],
                )
            # Heuristic minimum is a hard gate on the model's sufficiency verdict.
            coverage = assessed.coverage if minimum else "insufficient"
            needs = assessed.needs_more_research or not minimum or coverage != "sufficient"
            async with self.sessions() as session:
                session.add(
                    EvidenceAssessment(
                        agent_run_id=state["agent_run_id"],
                        iteration_number=state["iteration"],
                        subquestion_id=sub.id,
                        coverage=coverage,
                        deterministic_minimum_met=minimum,
                        needs_more_research=needs,
                        evidence_summary=assessed.evidence_summary[:1200],
                        missing_aspects_json=json.dumps(assessed.missing_aspects[:8]),
                        suggested_gap_types_json=json.dumps(assessed.suggested_gap_types[:8]),
                        evidence_chunk_ids_json=json.dumps(snap.get("chunk_ids", [])),
                        unique_document_count=len(snap.get("document_ids", [])),
                        unique_domain_count=len(snap.get("domains", [])),
                    )
                )
                await session.commit()
        await self._trace(state, "assess")
        return {}

    async def update_gaps(self, state: ResearchAgentState) -> dict:
        ids = []
        async with self.sessions() as session:
            assessments = (
                await session.execute(
                    select(EvidenceAssessment, ResearchSubquestion)
                    .join(
                        ResearchSubquestion,
                        ResearchSubquestion.id == EvidenceAssessment.subquestion_id,
                    )
                    .where(
                        EvidenceAssessment.agent_run_id == state["agent_run_id"],
                        EvidenceAssessment.iteration_number == state["iteration"],
                    )
                )
            ).all()
            for assessment, sub in assessments:
                existing = (
                    (
                        await session.execute(
                            select(ResearchGap).where(
                                ResearchGap.agent_run_id == state["agent_run_id"],
                                ResearchGap.subquestion_id == sub.id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if assessment.coverage == "sufficient" and not assessment.needs_more_research:
                    for gap in existing:
                        if gap.status in {"OPEN", "IN_PROGRESS"}:
                            gap.status = "RESOLVED"
                            gap.resolved_at = utc_now()
                            gap.resolution_iteration = state["iteration"]
                    continue
                missing = json.loads(assessment.missing_aspects_json)
                types = json.loads(assessment.suggested_gap_types_json)
                if not missing:
                    missing = ["Insufficient directly relevant evidence or source diversity"]
                if not types:
                    types = ["other"]
                for i, description in enumerate(missing[:4]):
                    description = " ".join(str(description).split())[:500]
                    kind = types[min(i, len(types) - 1)]
                    match = next(
                        (
                            g
                            for g in existing
                            if g.description == description and g.gap_type == kind
                        ),
                        None,
                    )
                    if match is None:
                        match = ResearchGap(
                            agent_run_id=state["agent_run_id"],
                            research_job_id=state["research_job_id"],
                            subquestion_id=sub.id,
                            iteration_created=state["iteration"],
                            gap_type=kind,
                            description=description,
                            priority=gap_priority(sub.priority, kind),
                            evidence_count=len(json.loads(assessment.evidence_chunk_ids_json)),
                        )
                        session.add(match)
                        await session.flush()
                        existing.append(match)
                    else:
                        match.evidence_count = len(json.loads(assessment.evidence_chunk_ids_json))
                        if match.status == "RESOLVED":
                            match.status = "OPEN"
                            match.resolved_at = None
                            match.resolution_iteration = None
                    if match.status in {"OPEN", "IN_PROGRESS"}:
                        ids.append(match.id)
            await session.commit()
        await self._trace(state, "update_gaps", open_gap_count=len(set(ids)))
        return {"unresolved_gap_ids": list(dict.fromkeys(ids))}

    async def check_stop(self, state: ResearchAgentState) -> dict:
        async with self.sessions() as session:
            run = await session.get(AgentRun, state["agent_run_id"])
            req = AgentRequest.model_validate_json(run.request_json)
            assessments = (
                (
                    await session.execute(
                        select(EvidenceAssessment).where(
                            EvidenceAssessment.agent_run_id == run.id,
                            EvidenceAssessment.iteration_number == state["iteration"],
                        )
                    )
                )
                .scalars()
                .all()
            )
            sufficient = sum(
                a.coverage == "sufficient"
                and not a.needs_more_research
                and a.deterministic_minimum_met
                for a in assessments
            )
            total = len(state["subquestion_ids"])
            previous = state.get("prior_signature", {})
            previous_sources = state.get("prior_source_signature", {})
            previous_coverage = state.get("prior_coverage", {})
            current = {
                key: value["chunk_ids"] for key, value in state.get("evidence_snapshot", {}).items()
            }
            current_sources = {
                key: value["document_ids"]
                for key, value in state.get("evidence_snapshot", {}).items()
            }
            current_coverage = {str(a.subquestion_id): a.coverage for a in assessments}
            rank = {"insufficient": 0, "partial": 1, "sufficient": 2}
            novel = (
                any(set(chunks) - set(previous.get(key, [])) for key, chunks in current.items())
                or any(
                    set(ids) - set(previous_sources.get(key, []))
                    for key, ids in current_sources.items()
                )
                or any(
                    rank.get(value, 0) > rank.get(previous_coverage.get(key), 0)
                    for key, value in current_coverage.items()
                )
            )
            resolved = (
                await session.scalar(
                    select(func.count())
                    .select_from(ResearchGap)
                    .where(
                        ResearchGap.agent_run_id == run.id,
                        ResearchGap.resolution_iteration == state["iteration"],
                    )
                )
            ) or 0
            stagnant = (
                0 if state["iteration"] == 0 or novel or resolved else state.get("stagnant", 0) + 1
            )
            high_gaps = (
                await session.scalar(
                    select(func.count())
                    .select_from(ResearchGap)
                    .where(
                        ResearchGap.agent_run_id == run.id,
                        ResearchGap.priority == "HIGH",
                        ResearchGap.status.in_(["OPEN", "IN_PROGRESS"]),
                    )
                )
            ) or 0
            reason = state.get("stop_reason")
            if not reason and run.cancel_requested:
                reason = "CANCELLED"
            if not reason and BudgetManager(req, run).remaining()["runtime_seconds"] <= 0:
                reason = "TIME_BUDGET"
            if not reason and sufficient == total and not high_gaps:
                reason = "SUFFICIENT_EVIDENCE"
            if not reason and stagnant >= self.settings.agent_max_stagnant_iterations:
                reason = "STAGNATION"
            if not reason:
                reason = BudgetManager(req, run).stop_reason()
            if not reason and not state.get("unresolved_gap_ids"):
                reason = "NO_ACTIONABLE_GAPS"
            metrics = {
                "subquestions_total": total,
                "subquestions_sufficient": sufficient,
                "subquestions_with_gaps": len(
                    {
                        g.subquestion_id
                        for g in (
                            (
                                await session.execute(
                                    select(ResearchGap).where(
                                        ResearchGap.id.in_(state.get("unresolved_gap_ids", []))
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                    }
                ),
                "coverage_rate": sufficient / total if total else 0,
                "stagnant_iterations": stagnant,
            }
            row = (
                await session.execute(
                    select(AgentIteration).where(
                        AgentIteration.agent_run_id == run.id,
                        AgentIteration.number == state["iteration"],
                    )
                )
            ).scalar_one_or_none()
            if row:
                row.status = "COMPLETED"
                row.completed_at = utc_now()
            await session.commit()
        await self._trace(state, "check_stop", reason=reason, **metrics)
        return {
            "stop_reason": reason,
            "metrics": metrics,
            "stagnant": stagnant,
            "prior_signature": current,
            "prior_source_signature": current_sources,
            "prior_coverage": current_coverage,
            "budget": BudgetManager(req, run).remaining(),
        }

    async def select_gap(self, state: ResearchAgentState) -> dict:
        async with self.sessions() as session:
            gaps = (
                (
                    await session.execute(
                        select(ResearchGap).where(
                            ResearchGap.id.in_(state["unresolved_gap_ids"]),
                            ResearchGap.status.in_(["OPEN", "IN_PROGRESS"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not gaps:
                return {"stop_reason": "NO_ACTIONABLE_GAPS"}
            gap = min(
                gaps, key=lambda g: (PRIORITY[g.priority], g.evidence_count, g.created_at, g.id)
            )
            gap.status = "IN_PROGRESS"
            run = await session.get(AgentRun, state["agent_run_id"])
            run.current_iteration = state["iteration"] + 1
            iteration = (
                await session.execute(
                    select(AgentIteration).where(
                        AgentIteration.agent_run_id == run.id,
                        AgentIteration.number == run.current_iteration,
                    )
                )
            ).scalar_one_or_none()
            if iteration is None:
                iteration = AgentIteration(agent_run_id=run.id, number=run.current_iteration)
                session.add(iteration)
            iteration.selected_gap_id = gap.id
            iteration.action = "SEARCH_GAP"
            await session.commit()
        next_state = {**state, "iteration": state["iteration"] + 1}
        await self._trace(
            next_state,
            "select_gap",
            gap_id=gap.id,
            priority=gap.priority,
            description=gap.description,
        )
        return {
            "iteration": state["iteration"] + 1,
            "selected_gap_id": gap.id,
            "action": "SEARCH_GAP",
            "pending_queries": [],
            "pending_seed_ids": [],
            "new_crawl_job_ids": [],
            "new_index_job_ids": [],
        }

    async def generate_queries(self, state: ResearchAgentState) -> dict:
        reason = await self._guard(state, "GAP_ANALYSIS")
        if reason:
            return {"stop_reason": reason}
        async with self.sessions() as session:
            gap = await session.get(ResearchGap, state["selected_gap_id"])
            sub = await session.get(ResearchSubquestion, gap.subquestion_id)
            job = await session.get(ResearchJob, state["research_job_id"])
            run = await session.get(AgentRun, state["agent_run_id"])
            req = AgentRequest.model_validate_json(run.request_json)
            prior = (
                (
                    await session.execute(
                        select(ResearchSearchQuery.query).where(
                            ResearchSearchQuery.research_job_id == job.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            existing = (
                (
                    await session.execute(
                        select(ResearchSearchQuery.query).where(
                            ResearchSearchQuery.agent_run_id == run.id,
                            ResearchSearchQuery.gap_id == gap.id,
                            ResearchSearchQuery.agent_iteration == state["iteration"],
                        )
                    )
                )
                .scalars()
                .all()
            )
        if existing:
            return {"pending_queries": list(existing)}
        capacity = min(req.max_queries_per_gap, req.max_new_search_queries - run.queries_used)
        if capacity <= 0:
            return {"stop_reason": "QUERY_BUDGET"}
        await self._llm_counter(state["agent_run_id"])
        try:
            plan = await self._within_time(
                state["agent_run_id"],
                self.query_generator.generate(
                    job.question,
                    {"plan_id": sub.plan_id, "question": sub.question},
                    {"id": gap.id, "description": gap.description},
                    list(prior),
                ),
            )
            candidates = plan.queries
        except TimeoutError:
            await self._llm_counter(state["agent_run_id"], failure=True)
            return {"stop_reason": "TIME_BUDGET"}
        except Exception as exc:
            await self._llm_counter(state["agent_run_id"], failure=True)
            logger.warning("query generation failed for gap %s: %s", gap.id, exc)
            # A local model outage must not leave an actionable gap inert.
            # This fallback can only feed the fixed search provider.
            candidates = [f"{sub.question} {gap.description}"]
        seen = {query_key(q) for q in prior}
        selected = []
        for candidate in candidates:
            candidate = " ".join(candidate.split())[:200]
            key = query_key(candidate)
            if len(candidate) >= 3 and key not in seen:
                seen.add(key)
                selected.append(candidate)
            if len(selected) == capacity:
                break
        async with self.sessions() as session:
            run = await session.get(AgentRun, state["agent_run_id"])
            for query in selected:
                row = ResearchSearchQuery(
                    research_job_id=state["research_job_id"],
                    query=query,
                    origin="AGENT_GAP",
                    agent_run_id=run.id,
                    gap_id=gap.id,
                    agent_iteration=state["iteration"],
                )
                session.add(row)
                await session.flush()
                session.add(
                    ResearchQuerySubquestion(
                        query_id=row.id, subquestion_id=sub.id, intent=gap.description
                    )
                )
            run.queries_used += len(selected)
            await session.commit()
        await self._trace(state, "generate_queries", queries=selected)
        return {"pending_queries": selected}

    async def search(self, state: ResearchAgentState) -> dict:
        reason = await self._guard(state, "SEARCHING")
        if reason:
            return {"stop_reason": reason}
        async with self.sessions() as session:
            queries = (
                (
                    await session.execute(
                        select(ResearchSearchQuery).where(
                            ResearchSearchQuery.agent_run_id == state["agent_run_id"],
                            ResearchSearchQuery.gap_id == state["selected_gap_id"],
                            ResearchSearchQuery.agent_iteration == state["iteration"],
                        )
                    )
                )
                .scalars()
                .all()
            )
        for query in queries:
            if query.status in {"completed", "failed"}:
                continue
            started = time.monotonic()
            error = None
            try:
                hits = await self._within_time(
                    state["agent_run_id"],
                    self.research.search_provider.search(
                        query.query, self.settings.agent_search_results_per_query
                    ),
                )
            except Exception as exc:
                hits = []
                error = f"{type(exc).__name__}: {exc}"[:1000]
            valid = []
            for hit in hits[: self.settings.agent_search_results_per_query]:
                try:
                    url = normalize_url(hit.url)
                    await self._within_time(
                        state["agent_run_id"], self.crawl.validator.validate(url)
                    )
                    valid.append((hit, url))
                except Exception as exc:
                    logger.info("agent search URL rejected: %s", exc)
            async with self.sessions() as session:
                row = await session.get(ResearchSearchQuery, query.id)
                row.status = "failed" if error else "completed"
                row.error_message = error
                row.result_count = len(hits)
                row.completed_at = utc_now()
                seen_urls = set()
                result_ids = []
                for hit, url in valid:
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    result = (
                        await session.execute(
                            select(ResearchSearchResult).where(
                                ResearchSearchResult.research_job_id == state["research_job_id"],
                                ResearchSearchResult.normalized_url == url,
                            )
                        )
                    ).scalar_one_or_none()
                    if result is None:
                        result = ResearchSearchResult(
                            research_job_id=state["research_job_id"],
                            normalized_url=url,
                            url=hit.url,
                            title=hit.title,
                            snippet=hit.snippet,
                            domain=hostname(url),
                            provider=hit.provider,
                            best_rank=hit.rank,
                        )
                        session.add(result)
                        await session.flush()
                    result_ids.append(result.id)
                    occurrence = (
                        await session.execute(
                            select(ResearchResultOccurrence).where(
                                ResearchResultOccurrence.result_id == result.id,
                                ResearchResultOccurrence.query_id == row.id,
                            )
                        )
                    ).scalar_one_or_none()
                    if occurrence is None:
                        session.add(
                            ResearchResultOccurrence(
                                result_id=result.id, query_id=row.id, rank=hit.rank
                            )
                        )
                run = await session.get(AgentRun, state["agent_run_id"])
                run.searches_used += 1
                session.add(
                    AgentAction(
                        agent_run_id=run.id,
                        iteration_number=state["iteration"],
                        gap_id=state["selected_gap_id"],
                        kind="SEARCH",
                        action_key=str(query.id),
                        status="FAILED" if error else "COMPLETED",
                        data_json=json.dumps(
                            {
                                "query": query.query,
                                "results": len(hits),
                                "valid_urls": len(seen_urls),
                                "result_ids": result_ids,
                            }
                        ),
                        error_message=error,
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                )
                await session.commit()
        await self._trace(state, "search", searches=len(queries))
        return {}

    async def select_seeds(self, state: ResearchAgentState) -> dict:
        reason = await self._guard(state, "GAP_ANALYSIS")
        if reason:
            return {"stop_reason": reason}
        async with self.sessions() as session:
            gap = await session.get(ResearchGap, state["selected_gap_id"])
            run = await session.get(AgentRun, state["agent_run_id"])
            req = AgentRequest.model_validate_json(run.request_json)
            queries = (
                (
                    await session.execute(
                        select(ResearchSearchQuery.id).where(
                            ResearchSearchQuery.agent_run_id == run.id,
                            ResearchSearchQuery.gap_id == gap.id,
                            ResearchSearchQuery.agent_iteration == state["iteration"],
                        )
                    )
                )
                .scalars()
                .all()
            )
            candidates = (
                (
                    await session.execute(
                        select(ResearchSearchResult, func.min(ResearchResultOccurrence.rank))
                        .join(
                            ResearchResultOccurrence,
                            ResearchResultOccurrence.result_id == ResearchSearchResult.id,
                        )
                        .where(ResearchResultOccurrence.query_id.in_(queries))
                        .group_by(ResearchSearchResult.id)
                    )
                ).all()
                if queries
                else []
            )
            existing_seeds = (
                await session.execute(
                    select(ResearchSeed, ResearchSearchResult)
                    .join(ResearchSearchResult, ResearchSearchResult.id == ResearchSeed.result_id)
                    .where(ResearchSeed.subquestion_id == gap.subquestion_id)
                )
            ).all()
            seed_action = (
                await session.execute(
                    select(AgentAction).where(
                        AgentAction.agent_run_id == run.id,
                        AgentAction.kind == "SEEDS",
                        AgentAction.action_key == str(state["iteration"]),
                    )
                )
            ).scalar_one_or_none()
            known_urls = set(
                (
                    await session.execute(
                        select(CrawledPage.normalized_url)
                        .join(ResearchSeed, ResearchSeed.crawl_job_id == CrawledPage.crawl_job_id)
                        .where(ResearchSeed.research_job_id == run.research_job_id)
                    )
                )
                .scalars()
                .all()
            )
        if seed_action:
            return {"pending_seed_ids": json.loads(seed_action.data_json)["seed_ids"]}
        existing_result_ids = {seed.result_id for seed, _ in existing_seeds}
        domains = defaultdict(int)
        for seed, result in existing_seeds:
            if seed.selected:
                domains[result.domain] += 1
        fresh = [
            (row, rank)
            for row, rank in candidates
            if row.id not in existing_result_ids and row.normalized_url not in known_urls
        ]
        if not fresh:
            await self._trace(state, "select_seeds", candidates=0, selected=0)
            return {"pending_seed_ids": []}
        try:
            embeddings = await self._within_time(
                state["agent_run_id"],
                self.crawl.embedding_provider.embed_many(
                    [result_representation(row) for row, _ in fresh]
                ),
            )
            query_vector = await self._within_time(
                state["agent_run_id"],
                self.crawl.embedding_provider.embed(gap.description),
            )
            scored = []
            for (row, rank), vector in zip(fresh, embeddings, strict=True):
                cosine = cosine_similarity(query_vector, vector)
                scored.append(
                    (seed_score(cosine, rank, self.settings.seed_semantic_weight), cosine, row)
                )
            scored.sort(key=lambda item: (-item[0], item[2].normalized_url))
        except TimeoutError:
            return {"stop_reason": "TIME_BUDGET"}
        except Exception as exc:
            logger.warning("agent seed scoring failed: %s", exc)
            await self._trace(state, "select_seeds", error=str(exc)[:300])
            return {"pending_seed_ids": []}
        capacity = req.max_new_seeds - run.seeds_used
        selected = []
        async with self.sessions() as session:
            run = await session.get(AgentRun, state["agent_run_id"])
            for score, cosine, result in scored:
                reason = None
                if len(selected) >= capacity:
                    reason = "SEED_BUDGET"
                elif domains[result.domain] >= self.settings.max_seeds_per_domain_per_subquestion:
                    reason = "DOMAIN_CAP"
                row = ResearchSeed(
                    research_job_id=run.research_job_id,
                    subquestion_id=gap.subquestion_id,
                    result_id=result.id,
                    semantic_relevance=cosine,
                    seed_score=score,
                    selected=reason is None,
                    rejection_reason=reason,
                )
                session.add(row)
                await session.flush()
                if reason is None:
                    selected.append(row.id)
                    domains[result.domain] += 1
            run.seeds_used += len(selected)
            session.add(
                AgentAction(
                    agent_run_id=run.id,
                    iteration_number=state["iteration"],
                    gap_id=gap.id,
                    kind="SEEDS",
                    action_key=str(state["iteration"]),
                    status="COMPLETED",
                    data_json=json.dumps({"seed_ids": selected, "candidates": len(scored)}),
                )
            )
            await session.commit()
        await self._trace(state, "select_seeds", candidates=len(scored), selected=len(selected))
        return {"pending_seed_ids": selected}

    async def crawl_seeds(self, state: ResearchAgentState) -> dict:
        reason = await self._guard(state, "CRAWLING")
        if reason:
            return {"stop_reason": reason}
        crawl_ids = []
        async with self.sessions() as session:
            gap = await session.get(ResearchGap, state["selected_gap_id"])
        for seed_id in state.get("pending_seed_ids", []):
            async with self.sessions() as session:
                run = await session.get(AgentRun, state["agent_run_id"])
                req = AgentRequest.model_validate_json(run.request_json)
                seed = await session.get(ResearchSeed, seed_id)
                result = await session.get(ResearchSearchResult, seed.result_id)
                action = (
                    await session.execute(
                        select(AgentAction).where(
                            AgentAction.agent_run_id == run.id,
                            AgentAction.kind == "CRAWL",
                            AgentAction.action_key == str(seed_id),
                        )
                    )
                ).scalar_one_or_none()
                remaining = req.max_new_pages - run.pages_used
                known = set(
                    (
                        await session.execute(
                            select(CrawledPage.normalized_url)
                            .join(
                                ResearchSeed, ResearchSeed.crawl_job_id == CrawledPage.crawl_job_id
                            )
                            .where(ResearchSeed.research_job_id == run.research_job_id)
                        )
                    )
                    .scalars()
                    .all()
                )
            if remaining <= 0:
                break
            if action and action.status in {"COMPLETED", "FAILED"} and seed.crawl_job_id:
                if json.loads(action.data_json).get("pages_crawled", 0):
                    crawl_ids.append(seed.crawl_job_id)
                continue
            if result.normalized_url in known and not seed.crawl_job_id:
                continue
            started = time.monotonic()
            try:
                if not seed.crawl_job_id:
                    # Persist the intent before launching, so a retry can find an
                    # already-created crawl after interruption at this boundary.
                    if action is None:
                        async with self.sessions() as session:
                            action = AgentAction(
                                agent_run_id=state["agent_run_id"],
                                iteration_number=state["iteration"],
                                gap_id=gap.id,
                                kind="CRAWL",
                                action_key=str(seed_id),
                                status="PENDING",
                                data_json=json.dumps({"url": result.normalized_url}),
                            )
                            session.add(action)
                            await session.commit()
                    async with self.sessions() as session:
                        recover = (
                            await session.execute(
                                select(CrawlJob)
                                .where(
                                    CrawlJob.start_url == result.normalized_url,
                                    CrawlJob.research_query == gap.description,
                                    CrawlJob.created_at >= action.created_at,
                                )
                                .order_by(CrawlJob.created_at)
                                .limit(1)
                            )
                        ).scalar_one_or_none()
                    crawl = recover or await self.crawl.start(
                        CrawlRequest(
                            start_url=result.normalized_url,
                            research_query=gap.description,
                            crawl_mode="intelligent",
                            max_depth=self.settings.agent_crawl_max_depth,
                            max_pages=min(remaining, self.settings.agent_max_pages_per_seed),
                            allow_external_domains=False,
                        ),
                        excluded_urls=known,
                    )
                    async with self.sessions() as session:
                        seed_row = await session.get(ResearchSeed, seed_id)
                        seed_row.crawl_job_id = crawl.id
                        stored_action = await session.get(AgentAction, action.id)
                        stored_action.status = "RUNNING"
                        stored_action.data_json = json.dumps(
                            {"crawl_job_id": crawl.id, "url": result.normalized_url}
                        )
                        await session.commit()
                crawl_id = seed.crawl_job_id or crawl.id
                while True:
                    progress = await self.crawl.repository.get_job(crawl_id)
                    if progress.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                        break
                    if await self._guard(state, "CRAWLING"):
                        break
                    await asyncio.sleep(0.2)
                async with self.sessions() as session:
                    attempts = (
                        await session.scalar(
                            select(func.count())
                            .select_from(CrawledPage)
                            .where(
                                CrawledPage.crawl_job_id == crawl_id,
                                or_(
                                    CrawledPage.error_message.is_(None),
                                    CrawledPage.error_message != "max_pages reached",
                                ),
                            )
                        )
                    ) or 0
                    pages = (
                        await session.scalar(
                            select(func.count())
                            .select_from(CrawledPage)
                            .where(
                                CrawledPage.crawl_job_id == crawl_id,
                                CrawledPage.crawl_status == "COMPLETED",
                            )
                        )
                    ) or 0
                    if pages:
                        crawl_ids.append(crawl_id)
                    run = await session.get(AgentRun, state["agent_run_id"])
                    run.pages_used += min(attempts, req.max_new_pages - run.pages_used)
                    run.pages_crawled += pages
                    action = (
                        await session.execute(
                            select(AgentAction).where(
                                AgentAction.agent_run_id == run.id,
                                AgentAction.kind == "CRAWL",
                                AgentAction.action_key == str(seed_id),
                            )
                        )
                    ).scalar_one()
                    action.status = "COMPLETED" if pages else "FAILED"
                    if not pages:
                        action.error_message = (
                            progress.error_message or "Crawl produced no completed pages"
                        )[:1000]
                    action.duration_ms = int((time.monotonic() - started) * 1000)
                    action.data_json = json.dumps(
                        {
                            "crawl_job_id": crawl_id,
                            "url": result.normalized_url,
                            "pages_crawled": pages,
                            "page_attempts": attempts,
                            "status": progress.status,
                        }
                    )
                    await session.commit()
            except Exception as exc:
                logger.warning("agent crawl failed for seed %s: %s", seed_id, exc)
                async with self.sessions() as session:
                    action = (
                        await session.execute(
                            select(AgentAction).where(
                                AgentAction.agent_run_id == state["agent_run_id"],
                                AgentAction.kind == "CRAWL",
                                AgentAction.action_key == str(seed_id),
                            )
                        )
                    ).scalar_one_or_none()
                    if action:
                        action.status = "FAILED"
                        action.error_message = str(exc)[:1000]
                        await session.commit()
        await self._trace(state, "crawl", crawl_job_ids=crawl_ids)
        return {"new_crawl_job_ids": crawl_ids}

    async def index_pages(self, state: ResearchAgentState) -> dict:
        reason = await self._guard(state, "INDEXING")
        if reason:
            return {"stop_reason": reason}
        if not state.get("new_crawl_job_ids"):
            await self._trace(state, "index", indexed=0)
            return {"new_index_job_ids": []}
        async with self.sessions() as session:
            action = (
                await session.execute(
                    select(AgentAction).where(
                        AgentAction.agent_run_id == state["agent_run_id"],
                        AgentAction.kind == "INDEX",
                        AgentAction.action_key == str(state["iteration"]),
                    )
                )
            ).scalar_one_or_none()
        if action and action.status in {"COMPLETED", "FAILED"}:
            prior = json.loads(action.data_json)
            return {
                "new_index_job_ids": [prior["index_job_id"]],
                "stop_reason": "INDEX_INCONSISTENT"
                if prior.get("consistency", {}).get("consistent") is False
                else None,
            }
        started = time.monotonic()
        if action is None:
            async with self.sessions() as session:
                action = AgentAction(
                    agent_run_id=state["agent_run_id"],
                    iteration_number=state["iteration"],
                    gap_id=state["selected_gap_id"],
                    kind="INDEX",
                    action_key=str(state["iteration"]),
                    status="PENDING",
                )
                session.add(action)
                await session.commit()
        index_id = json.loads(action.data_json).get("index_job_id")
        if not index_id:
            async with self.sessions() as session:
                recover = (
                    await session.execute(
                        select(IndexJob)
                        .where(
                            IndexJob.research_job_id == state["research_job_id"],
                            IndexJob.created_at >= action.created_at,
                        )
                        .order_by(IndexJob.created_at)
                        .limit(1)
                    )
                ).scalar_one_or_none()
            index_job = recover or await self.index.start(state["research_job_id"])
            index_id = index_job.id
            async with self.sessions() as session:
                stored_action = await session.get(AgentAction, action.id)
                stored_action.status = "RUNNING"
                stored_action.data_json = json.dumps({"index_job_id": index_id})
                await session.commit()
        while True:
            async with self.sessions() as session:
                index_job = await session.get(IndexJob, index_id)
                status = index_job.status
            if status in {"COMPLETED", "PARTIAL", "FAILED"}:
                break
            if await self._guard(state, "INDEXING"):
                return {"stop_reason": "TIME_BUDGET"}
            await asyncio.sleep(0.2)
        consistency = await check_consistency(
            self.sessions, self.index.vectors, state["research_job_id"]
        )
        async with self.sessions() as session:
            run = await session.get(AgentRun, state["agent_run_id"])
            run.documents_indexed += index_job.documents_indexed
            run.chunks_indexed += index_job.chunks_created
            action = (
                await session.execute(
                    select(AgentAction).where(
                        AgentAction.agent_run_id == run.id,
                        AgentAction.kind == "INDEX",
                        AgentAction.action_key == str(state["iteration"]),
                    )
                )
            ).scalar_one()
            action.status = "COMPLETED" if status == "COMPLETED" else "FAILED"
            if not consistency["consistent"]:
                action.status = "FAILED"
                action.error_message = "SQL, FTS and vector indexes are inconsistent"
            action.duration_ms = int((time.monotonic() - started) * 1000)
            action.data_json = json.dumps(
                {
                    "index_job_id": index_id,
                    "documents_indexed": index_job.documents_indexed,
                    "chunks_created": index_job.chunks_created,
                    "consistency": consistency,
                }
            )
            await session.commit()
        await self._trace(
            state,
            "index",
            index_job_id=index_id,
            documents_indexed=index_job.documents_indexed,
            chunks_created=index_job.chunks_created,
            consistency=consistency,
        )
        return {
            "new_index_job_ids": [index_id],
            "stop_reason": "INDEX_INCONSISTENT" if not consistency["consistent"] else None,
        }

    async def advance(self, state: ResearchAgentState) -> dict:
        await self._trace(state, "advance")
        return {
            "selected_gap_id": None,
            "pending_queries": [],
            "pending_seed_ids": [],
            "new_crawl_job_ids": [],
            "new_index_job_ids": [],
        }

    async def finalize(self, state: ResearchAgentState) -> dict:
        reason = state.get("stop_reason") or "ERROR"
        async with self.sessions() as session:
            run = await session.get(AgentRun, state["agent_run_id"])
            if run.status in TERMINAL:
                return {}
            assessments = (
                (
                    await session.execute(
                        select(EvidenceAssessment).where(
                            EvidenceAssessment.agent_run_id == run.id,
                            EvidenceAssessment.iteration_number == state["iteration"],
                        )
                    )
                )
                .scalars()
                .all()
            )
            has_evidence = any(json.loads(row.evidence_chunk_ids_json) for row in assessments)
            if reason == "SUFFICIENT_EVIDENCE":
                run.status = "COMPLETED"
            elif reason == "CANCELLED":
                run.status = "CANCELLED"
            elif reason in {
                "MAX_ITERATIONS",
                "QUERY_BUDGET",
                "SEED_BUDGET",
                "PAGE_BUDGET",
                "TIME_BUDGET",
            }:
                run.status = "BUDGET_EXHAUSTED"
            else:
                run.status = "PARTIAL" if has_evidence else "FAILED"
            run.stop_reason = reason
            run.current_node = "FINALIZE"
            run.completed_at = utc_now()
            if run.started_at:
                run.duration_ms = elapsed_ms(run.started_at, run.completed_at)
            if run.status == "BUDGET_EXHAUSTED":
                await session.execute(
                    ResearchGap.__table__.update()
                    .where(
                        ResearchGap.agent_run_id == run.id,
                        ResearchGap.status.in_(["OPEN", "IN_PROGRESS"]),
                    )
                    .values(status="BUDGET_EXHAUSTED")
                )
            await session.commit()
        await self._trace(state, "finalize", reason=reason)
        return {}

    async def cancel(self, run_id: str) -> AgentRun:
        async with self.sessions() as session:
            row = await session.get(AgentRun, run_id)
            if row is None:
                raise KeyError(run_id)
            if row.status not in TERMINAL:
                row.cancel_requested = True
                await session.commit()
            return row

    async def shutdown(self) -> None:
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
