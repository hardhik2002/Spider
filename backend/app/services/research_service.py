import asyncio
import json
import logging
import math
from collections import defaultdict

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.crawler.normalizer import InvalidURL, hostname, normalize_url
from app.crawler.scoring import cosine_similarity
from app.crawler.security import UnsafeTarget
from app.db.models import (
    CrawledPage,
    ResearchJob,
    ResearchQuerySubquestion,
    ResearchResultOccurrence,
    ResearchSearchQuery,
    ResearchSearchResult,
    ResearchSeed,
    ResearchSubquestion,
    utc_now,
)
from app.research.planner import ResearchPlanner
from app.research.search import SearchProvider
from app.schemas.crawl import CrawlRequest
from app.schemas.research import ResearchRequest, validate_plan
from app.services.crawl_service import CrawlService

logger = logging.getLogger("spidermind.research")
PRIORITY = {"high": 0, "medium": 1, "low": 2}


def seed_score(cosine: float, rank: int, semantic_weight: float) -> float:
    """Blend bounded cosine with a reciprocal-log rank prior; this is not a probability."""
    semantic_01 = (cosine + 1) / 2
    reciprocal_rank = 1 / math.log2(rank + 1)
    return semantic_weight * semantic_01 + (1 - semantic_weight) * reciprocal_rank


def result_representation(row: ResearchSearchResult) -> str:
    return (
        f"Title: {row.title[:300]}\nSnippet: {row.snippet[:1000]}\nURL: {row.normalized_url[:500]}"
    )


class ResearchService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings,
        crawl_service: CrawlService,
        planner: ResearchPlanner,
        search_provider: SearchProvider,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.crawl_service = crawl_service
        self.planner = planner
        self.search_provider = search_provider
        self.tasks: set[asyncio.Task] = set()

    async def start(self, request: ResearchRequest) -> ResearchJob:
        async with self.session_factory() as session:
            job = ResearchJob(
                question=request.question,
                request_json=request.model_dump_json(),
                max_subquestions=request.max_subquestions,
                max_total_pages=request.max_total_pages,
                planner_provider=self.planner.provider_name,
                planner_model=self.planner.model_name,
            )
            session.add(job)
            await session.commit()
        task = asyncio.create_task(self._run(job.id, request))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job

    async def _update(self, job_id: str, **values) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(ResearchJob).where(ResearchJob.id == job_id).values(**values)
            )
            await session.commit()

    async def _run(self, job_id: str, request: ResearchRequest) -> None:
        try:
            await self._update(job_id, status="running", stage="planning", started_at=utc_now())
            plan = validate_plan(await self.planner.plan(request), request)
            ordered = sorted(
                enumerate(plan.subquestions),
                key=lambda pair: (PRIORITY[pair[1].priority], pair[0]),
            )
            async with self.session_factory() as session:
                job = await session.get(ResearchJob, job_id)
                job.normalized_question = plan.normalized_question
                job.objective = plan.objective
                job.plan_json = plan.model_dump_json()
                subquestions = []
                for order, subplan in ordered:
                    row = ResearchSubquestion(
                        research_job_id=job_id,
                        plan_id=subplan.id,
                        question=subplan.question,
                        rationale=subplan.rationale,
                        priority=subplan.priority,
                        expected_evidence=json.dumps(subplan.expected_evidence),
                        preferred_source_types=json.dumps(subplan.preferred_source_types),
                        order=order,
                    )
                    session.add(row)
                    subquestions.append((row, subplan))
                await session.flush()
                query_by_key = {}
                query_subquestions: dict[int, set[int]] = defaultdict(set)
                for subrow, subplan in subquestions:
                    for planned_query in subplan.search_queries:
                        key = " ".join(planned_query.query.split()).casefold()
                        if key not in query_by_key:
                            query = ResearchSearchQuery(
                                research_job_id=job_id, query=planned_query.query.strip()
                            )
                            session.add(query)
                            await session.flush()
                            query_by_key[key] = query
                        query = query_by_key[key]
                        if subrow.id not in query_subquestions[query.id]:
                            session.add(
                                ResearchQuerySubquestion(
                                    query_id=query.id,
                                    subquestion_id=subrow.id,
                                    intent=planned_query.intent,
                                )
                            )
                            query_subquestions[query.id].add(subrow.id)
                job.total_search_queries = len(query_by_key)
                await session.commit()
            await self._update(job_id, stage="searching")
            query_rows = list(query_by_key.values())
            async with self.session_factory() as session:
                await session.execute(
                    update(ResearchSearchQuery)
                    .where(ResearchSearchQuery.research_job_id == job_id)
                    .values(status="running", started_at=utc_now())
                )
                await session.commit()
            semaphore = asyncio.Semaphore(self.settings.max_concurrent_searches)

            async def run_search(row: ResearchSearchQuery):
                async with semaphore:
                    try:
                        results = await self.search_provider.search(
                            row.query, request.search_results_per_query
                        )
                        return row, results[: request.search_results_per_query], None
                    except Exception as exc:
                        logger.warning("search failed for query %s: %s", row.id, exc)
                        return row, [], f"{type(exc).__name__}: {exc}"[:1000]

            search_outputs = await asyncio.gather(*(run_search(row) for row in query_rows))
            failed_query_ids = {row.id for row, _, error in search_outputs if error}
            candidate_urls = set()
            for _, items, _ in search_outputs:
                for item in items:
                    try:
                        candidate_urls.add(normalize_url(item.url))
                    except InvalidURL as exc:
                        logger.info("search result rejected: %s", exc)
            validation_semaphore = asyncio.Semaphore(
                max(2, self.settings.max_concurrent_searches * 2)
            )

            async def validate_url(url: str) -> str | None:
                async with validation_semaphore:
                    try:
                        return await self.crawl_service.validator.validate(url)
                    except (InvalidURL, UnsafeTarget, ValueError) as exc:
                        logger.info("search result rejected: %s", exc)
                        return None

            validated = await asyncio.gather(*(validate_url(url) for url in sorted(candidate_urls)))
            valid_urls = {url for url in validated if url is not None}
            async with self.session_factory() as session:
                result_by_url = {}
                associated: dict[int, set[int]] = defaultdict(set)
                rank_by_sub_result: dict[tuple[int, int], int] = {}
                raw_count = 0
                for query, results, error in search_outputs:
                    persisted_query = await session.get(ResearchSearchQuery, query.id)
                    persisted_query.completed_at = utc_now()
                    persisted_query.status = "failed" if error else "completed"
                    persisted_query.error_message = error
                    persisted_query.result_count = len(results)
                    raw_count += len(results)
                    seen_in_query = set()
                    for item in results:
                        try:
                            url = normalize_url(item.url)
                        except InvalidURL:
                            continue
                        if url not in valid_urls or url in seen_in_query:
                            continue
                        seen_in_query.add(url)
                        if url not in result_by_url:
                            result = ResearchSearchResult(
                                research_job_id=job_id,
                                normalized_url=url,
                                url=item.url,
                                title=item.title,
                                snippet=item.snippet,
                                domain=hostname(url),
                                provider=item.provider,
                                best_rank=item.rank,
                            )
                            session.add(result)
                            await session.flush()
                            result_by_url[url] = result
                        result = result_by_url[url]
                        result.best_rank = min(result.best_rank, item.rank)
                        session.add(
                            ResearchResultOccurrence(
                                result_id=result.id, query_id=query.id, rank=item.rank
                            )
                        )
                        for sub_id in query_subquestions[query.id]:
                            associated[sub_id].add(result.id)
                            key = (sub_id, result.id)
                            rank_by_sub_result[key] = min(
                                rank_by_sub_result.get(key, item.rank), item.rank
                            )
                job = await session.get(ResearchJob, job_id)
                job.total_search_results = raw_count
                job.unique_search_results = len(result_by_url)
                await session.commit()
            await self._update(job_id, stage="scoring")
            results = list(result_by_url.values())
            result_by_id = {row.id: row for row in results}
            vectors = {}
            if results:
                embeddings = await self.crawl_service.embedding_provider.embed_many(
                    [result_representation(row) for row in results]
                )
                if len(embeddings) != len(results):
                    raise ValueError("embedding provider returned wrong result count")
                vectors = dict(zip((row.id for row in results), embeddings, strict=True))
            selected: list[tuple[ResearchSeed, ResearchSubquestion, ResearchSearchResult]] = []
            async with self.session_factory() as session:
                for subrow, _ in subquestions:
                    candidate_ids = associated[subrow.id]
                    if not candidate_ids:
                        stored = await session.get(ResearchSubquestion, subrow.id)
                        stored.status = "failed"
                        stored.error_message = "No valid public search results"
                        continue
                    query_vector = await self.crawl_service.embedding_provider.embed(
                        subrow.question
                    )
                    candidates = []
                    for result_id in sorted(candidate_ids):
                        result = result_by_id[result_id]
                        similarity = cosine_similarity(query_vector, vectors[result_id])
                        score = seed_score(
                            similarity,
                            rank_by_sub_result[(subrow.id, result_id)],
                            self.settings.seed_semantic_weight,
                        )
                        candidates.append((score, similarity, result))
                    candidates.sort(key=lambda item: (-item[0], item[2].normalized_url))
                    domains: dict[str, int] = defaultdict(int)
                    selected_count = 0
                    for score, similarity, result in candidates:
                        reason = None
                        if selected_count >= request.seeds_per_subquestion:
                            reason = "SEED_LIMIT"
                        elif (
                            domains[result.domain]
                            >= self.settings.max_seeds_per_domain_per_subquestion
                        ):
                            reason = "DOMAIN_CAP"
                        seed = ResearchSeed(
                            research_job_id=job_id,
                            subquestion_id=subrow.id,
                            result_id=result.id,
                            semantic_relevance=similarity,
                            seed_score=score,
                            selected=reason is None,
                            rejection_reason=reason,
                        )
                        session.add(seed)
                        await session.flush()
                        if reason is None:
                            selected_count += 1
                            domains[result.domain] += 1
                            selected.append((seed, subrow, result))
                    stored = await session.get(ResearchSubquestion, subrow.id)
                    if selected_count:
                        had_search_failure = any(
                            subrow.id in sub_ids and query_id in failed_query_ids
                            for query_id, sub_ids in query_subquestions.items()
                        )
                        stored.status = "partial" if had_search_failure else "ready"
                    else:
                        stored.status = "failed"
                job = await session.get(ResearchJob, job_id)
                job.selected_seed_count = len(selected)
                await session.commit()
            await self._update(job_id, stage="crawling")
            crawled_urls: dict[str, str] = {}
            previously_crawled: set[str] = set()
            attempted_global = 0
            attempted_sub: dict[int, int] = defaultdict(int)
            pending_seeds: dict[int, int] = defaultdict(int)
            for _, subrow, _ in selected:
                pending_seeds[subrow.id] += 1
            for seed, subrow, result in selected:
                pending_seeds[subrow.id] -= 1
                if result.normalized_url in crawled_urls:
                    shared_crawl_id = crawled_urls[result.normalized_url]
                    await self._assign_crawl(seed.id, shared_crawl_id)
                    shared_state = await self.crawl_service.repository.get_job(shared_crawl_id)
                    async with self.session_factory() as session:
                        stored = await session.get(ResearchSubquestion, subrow.id)
                        if shared_state.pages_failed or not shared_state.pages_crawled:
                            stored.status = (
                                "partial"
                                if shared_state.pages_crawled or stored.status == "completed"
                                else "failed"
                            )
                            stored.error_message = (
                                shared_state.error_message or "Shared seed produced no useful pages"
                            )
                        elif stored.status == "ready":
                            stored.status = "completed"
                        elif stored.status == "failed":
                            stored.status = "partial"
                        await session.commit()
                    continue
                remaining_subquestion = request.max_pages_per_subquestion - attempted_sub[subrow.id]
                fair_seed_share = math.ceil(remaining_subquestion / (pending_seeds[subrow.id] + 1))
                remaining = min(request.max_total_pages - attempted_global, fair_seed_share)
                if remaining <= 0:
                    await self._reject_seed(seed.id, "PAGE_BUDGET")
                    async with self.session_factory() as session:
                        stored = await session.get(ResearchSubquestion, subrow.id)
                        if stored.status in {"ready", "completed"}:
                            stored.status = "partial"
                            stored.error_message = "Crawl page budget exhausted"
                        await session.commit()
                    continue
                try:
                    crawl = await self.crawl_service.start(
                        CrawlRequest(
                            start_url=result.normalized_url,
                            research_query=subrow.question,
                            crawl_mode="intelligent",
                            max_depth=request.max_depth,
                            max_pages=remaining,
                            allow_external_domains=False,
                        ),
                        excluded_urls=previously_crawled.copy(),
                    )
                    crawled_urls[result.normalized_url] = crawl.id
                    await self._assign_crawl(seed.id, crawl.id)
                    while True:
                        state = await self.crawl_service.repository.get_job(crawl.id)
                        if state.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                            break
                        await asyncio.sleep(0.1)
                    async with self.session_factory() as session:
                        attempts = (
                            await session.scalar(
                                select(func.count(CrawledPage.id)).where(
                                    CrawledPage.crawl_job_id == crawl.id,
                                    or_(
                                        CrawledPage.error_message.is_(None),
                                        CrawledPage.error_message != "max_pages reached",
                                    ),
                                )
                            )
                            or 0
                        )
                        attempted_global += attempts
                        attempted_sub[subrow.id] += attempts
                        fetched_pages = (
                            (
                                await session.execute(
                                    select(CrawledPage).where(CrawledPage.crawl_job_id == crawl.id)
                                )
                            )
                            .scalars()
                            .all()
                        )
                        for page in fetched_pages:
                            if page.status_code is not None:
                                previously_crawled.add(page.normalized_url)
                                if page.final_url:
                                    previously_crawled.add(page.final_url)
                        stored = await session.get(ResearchSubquestion, subrow.id)
                        if (
                            state.status != "COMPLETED"
                            or state.pages_failed
                            or not state.pages_crawled
                        ):
                            stored.status = (
                                "partial"
                                if state.pages_crawled or stored.status == "completed"
                                else "failed"
                            )
                            stored.error_message = state.error_message or (
                                f"{state.pages_failed} page attempts failed"
                                if state.pages_failed
                                else "Crawl produced no useful pages"
                            )
                        elif stored.status == "ready":
                            stored.status = "completed"
                        elif stored.status == "failed":
                            stored.status = "partial"
                        await session.commit()
                except Exception as exc:
                    logger.exception("seed crawl failed", extra={"research_job_id": job_id})
                    await self._reject_seed(seed.id, f"CRAWL_ERROR: {type(exc).__name__}"[:40])
                    async with self.session_factory() as session:
                        stored = await session.get(ResearchSubquestion, subrow.id)
                        stored.status = "partial"
                        stored.error_message = str(exc)[:1000]
                        await session.commit()
            async with self.session_factory() as session:
                job = await session.get(ResearchJob, job_id)
                crawl_ids = set(crawled_urls.values())
                if crawl_ids:
                    from app.db.models import CrawlJob

                    jobs = (
                        await session.execute(select(CrawlJob).where(CrawlJob.id.in_(crawl_ids)))
                    ).scalars()
                    job.pages_crawled = sum(row.pages_crawled for row in jobs)
                statuses = list(
                    (
                        await session.execute(
                            select(ResearchSubquestion.status).where(
                                ResearchSubquestion.research_job_id == job_id
                            )
                        )
                    ).scalars()
                )
                job.failed_subquestions = sum(value in {"failed", "partial"} for value in statuses)
                job.selected_seed_count = (
                    await session.scalar(
                        select(func.count(ResearchSeed.id)).where(
                            ResearchSeed.research_job_id == job_id,
                            ResearchSeed.selected.is_(True),
                        )
                    )
                    or 0
                )
                if statuses and all(value == "failed" for value in statuses):
                    job.status = "failed"
                elif job.failed_subquestions or failed_query_ids:
                    job.status = "partial"
                else:
                    job.status = "completed"
                job.stage = "completed"
                job.completed_at = utc_now()
                await session.commit()
        except asyncio.CancelledError:
            await self._update(
                job_id, status="cancelled", stage="cancelled", completed_at=utc_now()
            )
            raise
        except Exception as exc:
            logger.exception("research job failed", extra={"research_job_id": job_id})
            await self._update(
                job_id,
                status="failed",
                stage="failed",
                completed_at=utc_now(),
                error_message=f"{type(exc).__name__}: {exc}"[:1000],
            )

    async def _assign_crawl(self, seed_id: int, crawl_id: str) -> None:
        async with self.session_factory() as session:
            seed = await session.get(ResearchSeed, seed_id)
            seed.crawl_job_id = crawl_id
            await session.commit()

    async def _reject_seed(self, seed_id: int, reason: str) -> None:
        async with self.session_factory() as session:
            seed = await session.get(ResearchSeed, seed_id)
            seed.selected = False
            seed.rejection_reason = reason
            await session.commit()

    async def recover_jobs(self) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(ResearchJob)
                .where(ResearchJob.status.in_(["pending", "running"]))
                .values(
                    status="failed",
                    stage="failed",
                    completed_at=utc_now(),
                    error_message="Application restarted before research completed",
                )
            )
            await session.commit()

    async def shutdown(self) -> None:
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
