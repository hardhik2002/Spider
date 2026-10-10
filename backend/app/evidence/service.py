"""Bounded Phase 6 pipeline over already indexed research evidence."""

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent.models import AgentRun, EvidenceAssessment
from app.core.config import Settings
from app.db.models import ResearchJob, ResearchSubquestion, utc_now
from app.evidence.llm import (
    ClaimEquivalenceChecker,
    ClaimExtractor,
    CounterEvidenceQueryGenerator,
    EvidenceAdjudicator,
)
from app.evidence.logic import (
    cosine,
    decide_claim,
    normalize_claim,
    safe_to_merge,
    sentence_spans,
    source_groups,
    source_type,
    stable_claim_key,
    suspicious_instruction,
    validate_citation,
)
from app.evidence.models import (
    ClaimCitation,
    ClaimEvidenceRelation,
    ClaimOrigin,
    EvidenceJob,
    SourceGroup,
    VerifiedClaim,
)
from app.evidence.nli import EvidenceRelationClassifier
from app.evidence.schemas import (
    ClaimCandidate,
    ClaimStatus,
    ClaimType,
    DecisionLabel,
    EvidenceRelationResult,
    EvidenceRequest,
    RelationLabel,
)
from app.rag.models import KnowledgeChunk, KnowledgeChunkSource, KnowledgeDocument
from app.rag.retrieval import RetrievalService
from app.schemas.rag import RetrievalRequest

logger = logging.getLogger("spidermind.evidence")
_TOKEN = re.compile(r"[\w%-]+", re.UNICODE)
_TERMINAL = {"COMPLETED", "PARTIAL", "FAILED"}


def _clean_passage(text: str) -> str:
    return " ".join(
        sentence
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text)
        if not suspicious_instruction(sentence)
    ).strip()


def _origin_grounded(claim: str, passages: list[str]) -> bool:
    terms = {token for token in _TOKEN.findall(normalize_claim(claim)) if len(token) > 2}
    if not terms:
        return False
    for passage in passages:
        available = set(_TOKEN.findall(normalize_claim(_clean_passage(passage))))
        if len(terms & available) / len(terms) >= 0.4:
            return True
    return False


def _raw_decision(nli: EvidenceRelationResult) -> DecisionLabel:
    return {
        RelationLabel.ENTAILMENT: DecisionLabel.DIRECT_SUPPORT,
        RelationLabel.CONTRADICTION: DecisionLabel.CONTRADICTION,
        RelationLabel.NEUTRAL: DecisionLabel.NEUTRAL,
    }[nli.relation]


class EvidenceEngineService:
    def __init__(
        self,
        sessions: async_sessionmaker,
        settings: Settings,
        retrieval: RetrievalService,
        classifier: EvidenceRelationClassifier,
        extractor: ClaimExtractor,
        equivalence: ClaimEquivalenceChecker,
        counterqueries: CounterEvidenceQueryGenerator,
        adjudicator: EvidenceAdjudicator,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.retrieval = retrieval
        self.classifier = classifier
        self.extractor = extractor
        self.equivalence = equivalence
        self.counterqueries = counterqueries
        self.adjudicator = adjudicator
        self._tasks: dict[str, asyncio.Task] = {}
        self._work_lock = asyncio.Lock()

    async def start(self, research_job_id: str, request: EvidenceRequest) -> EvidenceJob:
        async with self.sessions() as session:
            job = await session.get(ResearchJob, research_job_id)
            if job is None:
                raise KeyError("Research job not found")
            if request.agent_run_id:
                agent = await session.get(AgentRun, request.agent_run_id)
                if agent is None or agent.research_job_id != research_job_id:
                    raise ValueError("Agent run does not belong to this research job")
                if agent.status not in {"COMPLETED", "BUDGET_EXHAUSTED", "PARTIAL"}:
                    raise ValueError("Agent run has not finished")
            chunks = (
                await session.execute(
                    select(KnowledgeChunk.id, KnowledgeChunk.text_hash)
                    .where(KnowledgeChunk.research_job_id == research_job_id)
                    .order_by(KnowledgeChunk.id)
                )
            ).all()
            if not chunks:
                raise ValueError("Research job has no indexed evidence")
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "job": research_job_id,
                        "request": request.model_dump(mode="json"),
                        "chunks": [(chunk_id, text_hash) for chunk_id, text_hash in chunks],
                        "nli_model": self.classifier.model_name,
                        "llm_model": getattr(
                            self.extractor, "model", type(self.extractor).__name__
                        ),
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            existing = (
                await session.execute(
                    select(EvidenceJob).where(
                        EvidenceJob.research_job_id == research_job_id,
                        EvidenceJob.input_fingerprint == fingerprint,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                if existing.status not in _TERMINAL and existing.id not in self._tasks:
                    self._schedule(existing.id)
                return existing
            row = EvidenceJob(
                research_job_id=research_job_id,
                agent_run_id=request.agent_run_id,
                input_fingerprint=fingerprint,
                request_json=request.model_dump_json(),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        self._schedule(row.id)
        return row

    def _schedule(self, evidence_job_id: str) -> None:
        task = asyncio.create_task(self._run(evidence_job_id))
        self._tasks[evidence_job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(evidence_job_id, None))

    async def recover_jobs(self) -> None:
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(EvidenceJob).where(EvidenceJob.status.in_(["PENDING", "RUNNING"]))
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                # A fresh run uses the same stable IDs and unique constraints.
                self._schedule(row.id)

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(self, evidence_job_id: str) -> None:
        started = time.monotonic()
        try:
            async with self.sessions() as session:
                row = await session.get(EvidenceJob, evidence_job_id)
                row.status, row.stage = "RUNNING", "EXTRACTION"
                row.started_at = row.started_at or utc_now()
                await session.commit()
                request = EvidenceRequest.model_validate_json(row.request_json)
                research_job_id = row.research_job_id
            logger.info(
                "evidence_job_started job_id=%s research_job_id=%s",
                evidence_job_id,
                research_job_id,
            )
            async with self._work_lock:
                counters, timings = await self._process(evidence_job_id, research_job_id, request)
            async with self.sessions() as session:
                row = await session.get(EvidenceJob, evidence_job_id)
                row.status, row.stage = "COMPLETED", "COMPLETED"
                row.counters_json, row.timings_json = json.dumps(counters), json.dumps(timings)
                row.completed_at = utc_now()
                row.duration_ms = int((time.monotonic() - started) * 1000)
                await session.commit()
            logger.info("evidence_job_completed job_id=%s", evidence_job_id)
        except asyncio.CancelledError:
            logger.info("evidence_job_interrupted job_id=%s", evidence_job_id)
            raise
        except Exception as exc:
            logger.exception("evidence_job_failed job_id=%s", evidence_job_id)
            async with self.sessions() as session:
                row = await session.get(EvidenceJob, evidence_job_id)
                row.status, row.stage = "FAILED", "FAILED"
                row.error_message = f"{type(exc).__name__}: {exc}"[:1000]
                row.completed_at = utc_now()
                row.duration_ms = int((time.monotonic() - started) * 1000)
                await session.commit()

    async def _process(
        self, evidence_job_id: str, research_job_id: str, request: EvidenceRequest
    ) -> tuple[dict, dict]:
        counters: Counter = Counter()
        timings: Counter = Counter()
        nli_pairs_before = self.classifier.pairs_classified
        nli_batches_before = self.classifier.nli_batches
        nli_ms_before = self.classifier.nli_duration_ms
        async with self.sessions() as session:
            subs = (
                (
                    await session.execute(
                        select(ResearchSubquestion)
                        .where(ResearchSubquestion.research_job_id == research_job_id)
                        .order_by(ResearchSubquestion.order)
                        .limit(request.max_subquestions)
                    )
                )
                .scalars()
                .all()
            )
            documents = (
                (
                    await session.execute(
                        select(KnowledgeDocument).where(
                            KnowledgeDocument.research_job_id == research_job_id,
                            KnowledgeDocument.index_status == "COMPLETED",
                        )
                    )
                )
                .scalars()
                .all()
            )
            chunks = (
                (
                    await session.execute(
                        select(KnowledgeChunk).where(
                            KnowledgeChunk.research_job_id == research_job_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            sources = (
                (
                    await session.execute(
                        select(KnowledgeChunkSource).where(
                            KnowledgeChunkSource.research_job_id == research_job_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        docs_by_id = {doc.id: doc for doc in documents}
        chunks_by_id = {chunk.id: chunk for chunk in chunks}
        sources_by_chunk: dict[int, list[KnowledgeChunkSource]] = defaultdict(list)
        for source in sources:
            if source.document_id in docs_by_id:
                sources_by_chunk[source.chunk_id].append(source)
        groups = source_groups(documents)
        group_docs: dict[str, list[int]] = defaultdict(list)
        for doc_id, group_key in groups.items():
            group_docs[group_key].append(doc_id)
        async with self.sessions() as session:
            for group_key, doc_ids in group_docs.items():
                group_id = hashlib.sha256(f"{evidence_job_id}:{group_key}".encode()).hexdigest()[
                    :36
                ]
                if await session.get(SourceGroup, group_id) is None:
                    session.add(
                        SourceGroup(
                            id=group_id,
                            evidence_job_id=evidence_job_id,
                            group_key=group_key,
                            document_ids_json=json.dumps(sorted(doc_ids)),
                            source_type=source_type(docs_by_id[doc_ids[0]].source_url),
                        )
                    )
            await session.commit()
        group_ids = {
            doc_id: hashlib.sha256(f"{evidence_job_id}:{key}".encode()).hexdigest()[:36]
            for doc_id, key in groups.items()
        }
        for sub in subs:
            counters["subquestions_processed"] += 1
            started = time.monotonic()
            pack = await self._extraction_pack(
                research_job_id, sub, request, chunks_by_id, sources_by_chunk
            )
            if not pack:
                continue
            logger.info(
                "claim_extraction_started job_id=%s subquestion_id=%s", evidence_job_id, sub.id
            )
            candidates = await self.extractor.extract(
                {
                    "plan_id": sub.plan_id,
                    "question": sub.question,
                    "max_claim_extraction_chunks": request.max_claim_extraction_chunks,
                    "max_chars_per_chunk": request.max_chars_per_chunk,
                    "max_claim_extraction_chars": request.max_claim_extraction_chars,
                },
                pack,
            )
            counters["llm_extraction_calls"] += 1
            permitted = {row["chunk_id"] for row in pack}
            candidates = [
                candidate
                for candidate in candidates
                if not suspicious_instruction(candidate.text)
                and set(candidate.originating_chunk_ids) <= permitted
                and _origin_grounded(
                    candidate.text,
                    [chunks_by_id[cid].text for cid in candidate.originating_chunk_ids],
                )
            ][: request.max_claims_per_subquestion]
            counters["claims_extracted"] += len(candidates)
            timings["claim_extraction_ms"] += int((time.monotonic() - started) * 1000)
            started = time.monotonic()
            normalized = await self._deduplicate(candidates, counters)
            counters["claims_normalized"] += len(candidates)
            counters["claims_deduplicated"] += len(candidates) - len(normalized)
            timings["claim_deduplication_ms"] += int((time.monotonic() - started) * 1000)
            for claim, origin_ids in normalized:
                claim_row = await self._persist_claim(
                    evidence_job_id,
                    research_job_id,
                    sub.id,
                    claim,
                    origin_ids,
                    chunks_by_id,
                    sources_by_chunk,
                    docs_by_id,
                )
                logger.info("claim_extracted job_id=%s claim_id=%s", evidence_job_id, claim_row.id)
                async with self.sessions() as session:
                    job_row = await session.get(EvidenceJob, evidence_job_id)
                    job_row.stage = "VERIFICATION"
                    await session.commit()
                await self._verify_claim(
                    claim_row,
                    request,
                    chunks_by_id,
                    sources_by_chunk,
                    docs_by_id,
                    group_ids,
                    counters,
                    timings,
                )
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(VerifiedClaim).where(
                            VerifiedClaim.evidence_job_id == evidence_job_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        for row in rows:
            counters[f"claims_{row.status.lower()}"] += 1
        counters["claims_processed"] = len(rows)
        counters["nli_pairs"] = self.classifier.pairs_classified - nli_pairs_before
        counters["nli_batches"] = self.classifier.nli_batches - nli_batches_before
        timings["nli_ms"] = self.classifier.nli_duration_ms - nli_ms_before
        async with self.sessions() as session:
            relation_rows = (
                await session.execute(
                    select(ClaimEvidenceRelation)
                    .join(VerifiedClaim, VerifiedClaim.id == ClaimEvidenceRelation.claim_id)
                    .where(VerifiedClaim.evidence_job_id == evidence_job_id)
                )
            ).scalars().all()
            citation_rows = (
                await session.execute(
                    select(ClaimCitation)
                    .join(VerifiedClaim, VerifiedClaim.id == ClaimCitation.claim_id)
                    .where(VerifiedClaim.evidence_job_id == evidence_job_id)
                )
            ).scalars().all()
        counters["relations_classified"] = len(relation_rows)
        counters["support_relations"] = sum(
            row.adjudicated_relation == DecisionLabel.DIRECT_SUPPORT for row in relation_rows
        )
        counters["contradiction_relations"] = sum(
            row.adjudicated_relation == DecisionLabel.CONTRADICTION for row in relation_rows
        )
        counters["neutral_relations"] = sum(
            row.adjudicated_relation == DecisionLabel.NEUTRAL for row in relation_rows
        )
        counters["citations_created"] = len(citation_rows)
        counters["citations_validated"] = len(citation_rows)
        return dict(counters), dict(timings)

    async def _extraction_pack(
        self, job_id, sub, request, chunks_by_id, sources_by_chunk
    ) -> list[dict]:
        result = await self.retrieval.retrieve(
            job_id,
            RetrievalRequest(
                query=sub.question,
                subquestion_id=sub.plan_id,
                retrieval_mode="hybrid",
                final_top_k=request.max_claim_extraction_chunks,
                rerank=False,
                include_neighbor_context=False,
            ),
        )
        ids = [row["chunk_id"] for row in result["results"]]
        if not ids:
            fallback = await self.retrieval.retrieve(
                job_id,
                RetrievalRequest(
                    query=sub.question,
                    retrieval_mode="hybrid",
                    final_top_k=request.max_claim_extraction_chunks,
                    rerank=False,
                    include_neighbor_context=False,
                ),
            )
            ids = [row["chunk_id"] for row in fallback["results"]]
        if request.agent_run_id:
            async with self.sessions() as session:
                latest = (
                    await session.execute(
                        select(EvidenceAssessment)
                        .where(
                            EvidenceAssessment.agent_run_id == request.agent_run_id,
                            EvidenceAssessment.subquestion_id == sub.id,
                        )
                        .order_by(EvidenceAssessment.iteration_number.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if latest:
                ids = json.loads(latest.evidence_chunk_ids_json) + ids
        pack = []
        used_groups = set()
        chars = 0
        for chunk_id in dict.fromkeys(ids):
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None or not sources_by_chunk.get(chunk_id):
                continue
            passage = _clean_passage(chunk.text)[: request.max_chars_per_chunk]
            if not passage:
                continue
            document_id = sources_by_chunk[chunk_id][0].document_id
            if document_id in used_groups and len(pack) >= request.max_claim_extraction_chunks // 2:
                continue
            if chars + len(passage) > request.max_claim_extraction_chars:
                passage = passage[: request.max_claim_extraction_chars - chars]
            if not passage:
                break
            pack.append({"chunk_id": chunk_id, "text": passage, "source_url": chunk.source_url})
            used_groups.add(document_id)
            chars += len(passage)
            if len(pack) >= request.max_claim_extraction_chunks:
                break
        return pack

    async def _deduplicate(self, candidates, counters) -> list[tuple[ClaimCandidate, set[int]]]:
        kept: list[tuple[ClaimCandidate, set[int]]] = []
        vectors = (
            await self.retrieval.embedder.embed_many([c.text for c in candidates])
            if candidates
            else []
        )
        for index, candidate in enumerate(candidates):
            normalized = normalize_claim(candidate.text)
            match = None
            for previous_index, (previous, _) in enumerate(kept):
                if normalized == normalize_claim(previous.text):
                    match = previous_index
                    break
                if (
                    candidate.temporal_scope != previous.temporal_scope
                    or set(candidate.qualifiers) != set(previous.qualifiers)
                    or set(candidate.conditions) != set(previous.conditions)
                ):
                    continue
                if not safe_to_merge(candidate.text, previous.text):
                    continue
                similarity = cosine(vectors[index], vectors[candidates.index(previous)])
                if similarity < self.settings.evidence_semantic_duplicate_threshold:
                    continue
                counters["llm_equivalence_calls"] += 1
                equivalent = await self.equivalence.equivalent(candidate.text, previous.text)
                if equivalent.same_claim:
                    match = previous_index
                    break
            if match is None:
                kept.append((candidate, set(candidate.originating_chunk_ids)))
            else:
                kept[match][1].update(candidate.originating_chunk_ids)
                logger.info("claim_deduplicated normalized=%s", normalized[:120])
        return kept

    async def _persist_claim(
        self,
        evidence_job_id,
        research_job_id,
        subquestion_id,
        candidate,
        origin_ids,
        chunks_by_id,
        sources_by_chunk,
        docs_by_id,
    ) -> VerifiedClaim:
        stable = stable_claim_key(research_job_id, subquestion_id, candidate.text)
        claim_id = hashlib.sha256(f"{evidence_job_id}:{stable}".encode()).hexdigest()[:36]
        async with self.sessions() as session:
            row = await session.get(VerifiedClaim, claim_id)
            if row is None:
                row = VerifiedClaim(
                    id=claim_id,
                    stable_key=stable,
                    evidence_job_id=evidence_job_id,
                    research_job_id=research_job_id,
                    subquestion_id=subquestion_id,
                    claim_text=candidate.text,
                    normalized_claim=normalize_claim(candidate.text),
                    claim_type=candidate.claim_type,
                    subject=candidate.subject,
                    predicate=candidate.predicate,
                    object=candidate.object,
                    qualifiers_json=json.dumps(candidate.qualifiers),
                    conditions_json=json.dumps(candidate.conditions),
                    temporal_scope=candidate.temporal_scope,
                )
                session.add(row)
            for chunk_id in origin_ids:
                for source in sources_by_chunk.get(chunk_id, []):
                    document = docs_by_id[source.document_id]
                    exists = (
                        await session.execute(
                            select(ClaimOrigin.id).where(
                                ClaimOrigin.claim_id == claim_id,
                                ClaimOrigin.chunk_id == chunk_id,
                                ClaimOrigin.document_id == document.id,
                            )
                        )
                    ).first()
                    if not exists:
                        session.add(
                            ClaimOrigin(
                                claim_id=claim_id,
                                chunk_id=chunk_id,
                                document_id=document.id,
                                source_url=document.source_url,
                            )
                        )
            await session.commit()
        return row

    async def _retrieve_candidates(self, claim: VerifiedClaim, request, counters, timings):
        queries = [claim.claim_text]
        if request.use_counterqueries:
            started = time.monotonic()
            plan = await self.counterqueries.generate(claim.claim_text)
            counters["llm_counterquery_calls"] += 1
            for query in plan.queries[:2]:
                query = query.strip()[:400]
                if query and query.casefold() not in {item.casefold() for item in queries}:
                    queries.append(query)
            timings["counter_retrieval_ms"] += int((time.monotonic() - started) * 1000)
        selected = {}
        for index, query in enumerate(queries):
            started = time.monotonic()
            result = await self.retrieval.retrieve(
                claim.research_job_id,
                RetrievalRequest(
                    query=query,
                    retrieval_mode="hybrid",
                    dense_top_k=self.settings.evidence_claim_dense_top_k,
                    lexical_top_k=self.settings.evidence_claim_lexical_top_k,
                    fusion_top_k=(
                        min(
                            self.settings.evidence_claim_fusion_top_k,
                            self.settings.evidence_claim_rerank_top_k,
                        )
                        if request.rerank
                        else self.settings.evidence_claim_fusion_top_k
                    ),
                    final_top_k=min(request.evidence_candidates_per_claim, 50),
                    rerank=request.rerank,
                    include_neighbor_context=False,
                ),
            )
            timings["retrieval_ms" if index == 0 else "counter_retrieval_ms"] += int(
                (time.monotonic() - started) * 1000
            )
            for row in result["results"]:
                selected.setdefault(row["chunk_id"], row)
            if index == 0:
                counters["claim_only_candidate_chunks"] += len(selected)
        counters["combined_candidate_chunks"] += len(selected)
        return list(selected.values())[: request.evidence_candidates_per_claim]

    def _needs_adjudication(self, claim, nli, conflict: bool, request) -> bool:
        if not request.use_llm_adjudication or not self.settings.evidence_adjudication_enabled:
            return False
        if request.adjudicate_all or self.settings.evidence_adjudicate_all:
            return True
        scores = sorted(
            [nli.entailment_score, nli.neutral_score, nli.contradiction_score], reverse=True
        )
        return (
            scores[0] - scores[1] < self.settings.evidence_adjudication_margin_threshold
            or claim.claim_type
            in {ClaimType.NUMERIC, ClaimType.TEMPORAL, ClaimType.COMPARATIVE, ClaimType.PERFORMANCE}
            or conflict
        )

    async def _citation_span(self, claim: str, chunk_text: str, decision: DecisionLabel):
        if decision not in {DecisionLabel.DIRECT_SUPPORT, DecisionLabel.CONTRADICTION}:
            return None
        sentences = [item for item in sentence_spans(chunk_text) if len(item[2]) >= 12][:12]
        if not sentences:
            return None
        expected = (
            RelationLabel.ENTAILMENT
            if decision == DecisionLabel.DIRECT_SUPPORT
            else RelationLabel.CONTRADICTION
        )
        scores = await self.classifier.classify_many(
            [(sentence, claim) for _, _, sentence in sentences]
        )
        candidates = []
        for (start, end, sentence), score in zip(sentences, scores, strict=True):
            strength = (
                score.entailment_score
                if expected == RelationLabel.ENTAILMENT
                else score.contradiction_score
            )
            if score.relation == expected and strength >= 0.5:
                candidates.append((len(sentence), -strength, start, end, sentence))
        if not candidates:
            return None
        _, _, start, end, exact = min(candidates)
        if not validate_citation(chunk_text, start, end, exact):
            return None
        return start, end, exact

    async def _verify_claim(
        self,
        claim,
        request,
        chunks_by_id,
        sources_by_chunk,
        docs_by_id,
        group_ids,
        counters,
        timings,
    ) -> None:
        logger.info(
            "claim_verification_started job_id=%s claim_id=%s", claim.evidence_job_id, claim.id
        )
        candidates = await self._retrieve_candidates(claim, request, counters, timings)
        # Verification searches the whole indexed corpus, including non-origin passages.
        candidate_rows = [row for row in candidates if row["chunk_id"] in chunks_by_id]
        pairs = [
            (chunks_by_id[row["chunk_id"]].text[:3000], claim.claim_text) for row in candidate_rows
        ]
        started = time.monotonic()
        if request.use_nli:
            predictions = await self.classifier.classify_many(pairs)
        else:
            predictions = [
                EvidenceRelationResult(
                    relation=RelationLabel.NEUTRAL,
                    entailment_score=0,
                    neutral_score=1,
                    contradiction_score=0,
                    model_name="DISABLED",
                )
                for _ in pairs
            ]
        timings["nli_ms"] += int((time.monotonic() - started) * 1000)
        counters["nli_pairs"] += len(pairs) if request.use_nli else 0
        conflict = {RelationLabel.ENTAILMENT, RelationLabel.CONTRADICTION} <= {
            row.relation for row in predictions
        }
        async with self.sessions() as session:
            existing = (
                (
                    await session.execute(
                        select(ClaimEvidenceRelation).where(
                            ClaimEvidenceRelation.claim_id == claim.id
                        )
                    )
                )
                .scalars()
                .all()
            )
        seen = {(row.chunk_id, row.document_id) for row in existing}
        for rank, (candidate, nli) in enumerate(zip(candidate_rows, predictions, strict=True), 1):
            chunk_id = candidate["chunk_id"]
            chunk = chunks_by_id[chunk_id]
            decision = _raw_decision(nli)
            summary = None
            mismatch = False
            if self._needs_adjudication(claim, nli, conflict, request):
                started = time.monotonic()
                try:
                    adjudicated = await self.adjudicator.adjudicate(
                        claim.claim_text, chunk.text[:3000], nli
                    )
                    decision, summary = adjudicated.relation, adjudicated.decision_summary
                    mismatch = adjudicated.important_qualifier_mismatch
                    counters["llm_adjudication_calls"] += 1
                    logger.info(
                        "evidence_adjudicated job_id=%s claim_id=%s chunk_id=%s",
                        claim.evidence_job_id,
                        claim.id,
                        chunk_id,
                    )
                except Exception as exc:
                    counters["llm_adjudication_failures"] += 1
                    logger.warning(
                        "adjudication_failed claim_id=%s chunk_id=%s error=%s",
                        claim.id,
                        chunk_id,
                        type(exc).__name__,
                    )
                    decision = DecisionLabel.UNCLEAR
                timings["adjudication_ms"] += int((time.monotonic() - started) * 1000)
            if mismatch and decision in {DecisionLabel.DIRECT_SUPPORT, DecisionLabel.CONTRADICTION}:
                decision = (
                    DecisionLabel.PARTIAL_SUPPORT
                    if decision == DecisionLabel.DIRECT_SUPPORT
                    else DecisionLabel.UNCLEAR
                )
            citation = None
            if request.use_nli and decision in {
                DecisionLabel.DIRECT_SUPPORT,
                DecisionLabel.CONTRADICTION,
            }:
                started = time.monotonic()
                citation = await self._citation_span(claim.claim_text, chunk.text, decision)
                timings["citation_validation_ms"] += int((time.monotonic() - started) * 1000)
                if citation is None:
                    counters["citation_failures"] += 1
                    logger.info(
                        "citation_validation_failed job_id=%s claim_id=%s chunk_id=%s",
                        claim.evidence_job_id,
                        claim.id,
                        chunk_id,
                    )
            for source in sources_by_chunk.get(chunk_id, []):
                document = docs_by_id[source.document_id]
                key = (chunk_id, document.id)
                if key in seen:
                    continue
                async with self.sessions() as session:
                    relation = ClaimEvidenceRelation(
                        claim_id=claim.id,
                        chunk_id=chunk_id,
                        document_id=document.id,
                        source_group_id=group_ids[document.id],
                        source_url=document.source_url,
                        source_domain=document.source_domain,
                        retrieval_rank=rank,
                        retrieval_score=candidate.get("rrf_score"),
                        nli_relation=nli.relation,
                        entailment_score=nli.entailment_score,
                        neutral_score=nli.neutral_score,
                        contradiction_score=nli.contradiction_score,
                        nli_model_name=nli.model_name,
                        adjudicated_relation=decision,
                        adjudication_summary=summary,
                        qualifier_mismatch=mismatch,
                        is_citation_candidate=citation is not None,
                    )
                    session.add(relation)
                    await session.flush()
                    if citation:
                        start, end, exact = citation
                        session.add(
                            ClaimCitation(
                                claim_id=claim.id,
                                relation_id=relation.id,
                                chunk_id=chunk_id,
                                document_id=document.id,
                                source_url=document.source_url,
                                start_offset=start,
                                end_offset=end,
                                exact_text=exact,
                                relation=(
                                    "SUPPORTING"
                                    if decision == DecisionLabel.DIRECT_SUPPORT
                                    else "CONTRADICTING"
                                ),
                            )
                        )
                        counters["citations_created"] += 1
                        counters["citations_validated"] += 1
                        logger.info(
                            "citation_created job_id=%s claim_id=%s chunk_id=%s",
                            claim.evidence_job_id,
                            claim.id,
                            chunk_id,
                        )
                    await session.commit()
                seen.add(key)
                counters[f"{decision.lower()}_relations"] += 1
                counters["relations_classified"] += 1
                logger.info(
                    "evidence_relation_classified job_id=%s claim_id=%s chunk_id=%s relation=%s",
                    claim.evidence_job_id,
                    claim.id,
                    chunk_id,
                    decision,
                )
        await self._assign_status(claim.id)

    async def _assign_status(self, claim_id: str) -> None:
        async with self.sessions() as session:
            claim = await session.get(VerifiedClaim, claim_id)
            relations = (
                (
                    await session.execute(
                        select(ClaimEvidenceRelation).where(
                            ClaimEvidenceRelation.claim_id == claim_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            citation_rows = (
                (
                    await session.execute(
                        select(ClaimCitation).where(ClaimCitation.claim_id == claim_id)
                    )
                )
                .scalars()
                .all()
            )
            cited = {row.relation_id for row in citation_rows}
            inputs = [
                {
                    "source_group_id": row.source_group_id,
                    "relation": row.adjudicated_relation,
                    "valid_citation": row.id in cited,
                }
                for row in relations
            ]
            decision = decide_claim(inputs)
            claim.status = decision["status"]
            claim.confidence_tier = decision["confidence"]
            claim.support_count = sum(
                r.adjudicated_relation == DecisionLabel.DIRECT_SUPPORT for r in relations
            )
            claim.contradiction_count = sum(
                r.adjudicated_relation == DecisionLabel.CONTRADICTION for r in relations
            )
            claim.partial_count = sum(
                r.adjudicated_relation == DecisionLabel.PARTIAL_SUPPORT for r in relations
            )
            claim.independent_support_groups = decision["support_groups"]
            claim.independent_contradiction_groups = decision["contradiction_groups"]
            claim.needs_more_verification = decision["needs_more_verification"]
            claim.verification_gap_reason = (
                "Independent support and contradiction both present"
                if claim.status == ClaimStatus.CONTESTED
                else "No validated direct support or contradiction"
                if claim.status == ClaimStatus.INSUFFICIENT_EVIDENCE
                else None
            )
            claim.decision_metadata_json = json.dumps(
                {
                    "rule": "cited-independent-source-groups-v1",
                    "nli_scores_are_truth_probabilities": False,
                }
            )
            await session.commit()
        logger.info("claim_status_assigned claim_id=%s status=%s", claim_id, claim.status)
        logger.info(
            "claim_confidence_assigned claim_id=%s tier=%s", claim_id, claim.confidence_tier
        )
        if claim.status == ClaimStatus.CONTESTED:
            logger.info("contradiction_detected claim_id=%s", claim_id)
