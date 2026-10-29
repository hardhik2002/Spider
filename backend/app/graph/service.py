"""In-process graph build lifecycle over one evidence job and research job."""

import asyncio
import json
import logging
import time
from collections import Counter

from sqlalchemy import select, update

from app.db.models import utc_now
from app.graph.builder import GraphBuilder
from app.graph.models import GraphBuildJob, GraphSnapshot
from app.graph.projection import GraphProjection
from app.graph.store import SQLiteGraphStore
from app.graph.types import GRAPH_SCHEMA_VERSION
from app.graph.validation import GraphValidator

logger = logging.getLogger("spidermind.graph")


class GraphService:
    def __init__(self, sessions, settings) -> None:
        self.sessions = sessions
        self.settings = settings
        self.builder = GraphBuilder(sessions)
        self.store = SQLiteGraphStore(sessions)
        self.validator = GraphValidator()
        self.tasks: set[asyncio.Task] = set()
        self._start_lock = asyncio.Lock()
        self._build_locks: dict[str, asyncio.Lock] = {}

    async def start(
        self,
        research_job_id: str,
        evidence_job_id: str,
        include_uncited_relations: bool | None = None,
    ) -> tuple[GraphBuildJob, GraphSnapshot]:
        include = (
            self.settings.graph_include_uncited_relations
            if include_uncited_relations is None
            else include_uncited_relations
        )
        state = await self.builder.load(research_job_id, evidence_job_id)
        fingerprint = state.fingerprint(include)
        async with self._start_lock:
            snapshot = await self.store.find_fingerprint(
                research_job_id, evidence_job_id, fingerprint
            )
            if snapshot is not None:
                async with self.sessions() as session:
                    existing = (
                        (
                            await session.execute(
                                select(GraphBuildJob)
                                .where(GraphBuildJob.snapshot_id == snapshot.id)
                                .order_by(GraphBuildJob.started_at.desc(), GraphBuildJob.id.desc())
                            )
                        )
                        .scalars()
                        .first()
                    )
                if snapshot.status in {"COMPLETED", "RUNNING", "PENDING"} and existing:
                    return existing, snapshot
            if snapshot is None:
                snapshot = GraphSnapshot(
                    research_job_id=research_job_id,
                    evidence_job_id=evidence_job_id,
                    graph_schema_version=GRAPH_SCHEMA_VERSION,
                    input_fingerprint=fingerprint,
                    include_uncited_relations=include,
                )
                await self.store.create_snapshot(snapshot)
            async with self.sessions() as session:
                row = await session.get(GraphSnapshot, snapshot.id)
                row.status = "PENDING"
                job = GraphBuildJob(
                    research_job_id=research_job_id,
                    evidence_job_id=evidence_job_id,
                    snapshot_id=snapshot.id,
                )
                session.add(job)
                await session.commit()
            task = asyncio.create_task(self._run(job.id, include))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return job, snapshot

    async def _run(self, job_id: str, include_uncited_relations: bool) -> None:
        started = time.monotonic()
        async with self.sessions() as session:
            job = await session.get(GraphBuildJob, job_id)
            snapshot_id = job.snapshot_id
            research_job_id = job.research_job_id
            evidence_job_id = job.evidence_job_id
        lock = self._build_locks.setdefault(snapshot_id, asyncio.Lock())
        async with lock:
            timings: dict[str, int] = {}
            try:
                async with self.sessions() as session:
                    job = await session.get(GraphBuildJob, job_id)
                    snapshot = await session.get(GraphSnapshot, snapshot_id)
                    job.status = snapshot.status = "RUNNING"
                    job.started_at = utc_now()
                    await session.commit()
                mark = time.monotonic()
                state = await self.builder.load(research_job_id, evidence_job_id)
                timings["source_load_ms"] = int((time.monotonic() - mark) * 1000)
                if state.fingerprint(include_uncited_relations) != snapshot.input_fingerprint:
                    raise ValueError(
                        "Evidence changed while graph build was pending; start a new build"
                    )
                mark = time.monotonic()
                material = self.builder.build(
                    state, include_uncited_relations=include_uncited_relations
                )
                timings.update(material.timings)
                timings["materialization_ms"] = int((time.monotonic() - mark) * 1000)
                mark = time.monotonic()
                counts = await self.store.replace(snapshot_id, material.nodes, material.edges)
                timings["database_write_ms"] = int((time.monotonic() - mark) * 1000)
                mark = time.monotonic()
                projection = GraphProjection(
                    await self.store.list_nodes(snapshot_id),
                    await self.store.list_edges(snapshot_id),
                )
                timings["projection_ms"] = int((time.monotonic() - mark) * 1000)
                mark = time.monotonic()
                validation = self.validator.validate(state, material, projection)
                timings["validation_ms"] = int((time.monotonic() - mark) * 1000)
                timings["total_ms"] = int((time.monotonic() - started) * 1000)
                node_counts = Counter(node["type"] for node in projection.nodes.values())
                edge_counts = Counter(edge["type"] for edge in projection.edges.values())
                status = "COMPLETED" if validation["status"] == "VALID" else "PARTIAL"
                async with self.sessions() as session:
                    job = await session.get(GraphBuildJob, job_id)
                    snapshot = await session.get(GraphSnapshot, snapshot_id)
                    job.status = snapshot.status = status
                    job.nodes_created = counts["nodes_created"]
                    job.nodes_reused = counts["nodes_reused"]
                    job.edges_created = counts["edges_created"]
                    job.edges_reused = counts["edges_reused"]
                    job.validation_errors = len(validation["issues"])
                    job.timings_json = json.dumps(timings)
                    job.completed_at = snapshot.completed_at = utc_now()
                    job.duration_ms = snapshot.duration_ms = timings["total_ms"]
                    snapshot.graph_content_hash = material.content_hash
                    snapshot.node_count = len(projection.nodes)
                    snapshot.edge_count = len(projection.edges)
                    snapshot.nodes_by_type_json = json.dumps(dict(sorted(node_counts.items())))
                    snapshot.edges_by_type_json = json.dumps(dict(sorted(edge_counts.items())))
                    snapshot.validation_status = validation["status"]
                    snapshot.validation_issues_json = json.dumps(validation["issues"])
                    snapshot.metrics_json = json.dumps(
                        {
                            key: value
                            for key, value in validation.items()
                            if key not in {"issues", "nodes_by_type", "edges_by_type"}
                        }
                    )
                    snapshot.timings_json = json.dumps(timings)
                    await session.commit()
            except asyncio.CancelledError:
                await self._finish_error(
                    job_id, "PARTIAL", "Build interrupted by shutdown", started
                )
                raise
            except Exception as exc:
                logger.exception("graph build failed", extra={"research_job_id": research_job_id})
                await self._finish_error(job_id, "FAILED", f"{type(exc).__name__}: {exc}", started)

    async def _finish_error(self, job_id: str, status: str, message: str, started: float):
        async with self.sessions() as session:
            job = await session.get(GraphBuildJob, job_id)
            snapshot = await session.get(GraphSnapshot, job.snapshot_id)
            job.status = snapshot.status = status
            job.error_message = message[:1000]
            job.completed_at = snapshot.completed_at = utc_now()
            job.duration_ms = snapshot.duration_ms = int((time.monotonic() - started) * 1000)
            snapshot.validation_status = "PARTIAL"
            await session.commit()

    async def recover_jobs(self) -> None:
        async with self.sessions() as session:
            await session.execute(
                update(GraphBuildJob)
                .where(GraphBuildJob.status.in_(["PENDING", "RUNNING"]))
                .values(
                    status="PARTIAL",
                    completed_at=utc_now(),
                    error_message="Interrupted by process restart; start graph build again",
                )
            )
            await session.execute(
                update(GraphSnapshot)
                .where(GraphSnapshot.status.in_(["PENDING", "RUNNING"]))
                .values(status="PARTIAL", validation_status="PARTIAL", completed_at=utc_now())
            )
            await session.commit()

    async def snapshot(self, research_job_id: str, snapshot_id: str) -> GraphSnapshot:
        row = await self.store.get_snapshot(snapshot_id)
        if row is None or row.research_job_id != research_job_id:
            raise KeyError("Graph snapshot not found")
        return row

    async def consistency(self, research_job_id: str, snapshot_id: str) -> dict:
        snapshot = await self.snapshot(research_job_id, snapshot_id)
        state = await self.builder.load(research_job_id, snapshot.evidence_job_id)
        include = snapshot.include_uncited_relations
        expected = self.builder.build(state, include_uncited_relations=include)
        actual = GraphProjection(
            await self.store.list_nodes(snapshot_id), await self.store.list_edges(snapshot_id)
        )
        result = self.validator.validate(state, expected, actual)
        result["snapshot_id"] = snapshot_id
        result["input_fingerprint_matches"] = (
            state.fingerprint(include) == snapshot.input_fingerprint
        )
        if not result["input_fingerprint_matches"]:
            result["status"] = "INVALID"
            result["issues"].append({"code": "SOURCE_EVIDENCE_CHANGED"})
        return result

    async def shutdown(self) -> None:
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
