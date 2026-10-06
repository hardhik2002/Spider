"""Transparent controlled metrics; labels come from fixtures, never from model claims."""

import json
import statistics
from collections import Counter
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import CrawledPage, CrawlJob, ResearchJob, ResearchSearchResult, ResearchSeed
from app.schemas.research import ResearchPlan


def _tokens(text: str) -> set[str]:
    return {word.strip(".,:;!?()[]{}") for word in text.casefold().split() if word.strip()}


def _overlap(left: str, right: str) -> float:
    first, second = _tokens(left), _tokens(right)
    return len(first & second) / len(first | second) if first or second else 1.0


def plan_metrics(plan: ResearchPlan, expected_facets: dict[str, list[str]]) -> dict:
    """Facet aliases match text; redundancy/diversity use token Jaccard >= 0.8."""
    texts = [
        " ".join(
            [
                sub.question,
                *sub.expected_evidence,
                *sub.preferred_source_types,
                *(query.query for query in sub.search_queries),
            ]
        ).casefold()
        for sub in plan.subquestions
    ]
    represented = [
        facet
        for facet, aliases in expected_facets.items()
        if any(alias.casefold() in text for alias in aliases for text in texts)
    ]
    questions = [sub.question for sub in plan.subquestions]
    duplicate_questions = sum(_overlap(a, b) >= 0.8 for a, b in combinations(questions, 2))
    queries = [query.query for sub in plan.subquestions for query in sub.search_queries]
    duplicate_queries = sum(_overlap(a, b) >= 0.8 for a, b in combinations(queries, 2))
    question_pairs = len(questions) * (len(questions) - 1) // 2
    query_pairs = len(queries) * (len(queries) - 1) // 2
    return {
        "facet_coverage": len(represented) / len(expected_facets) if expected_facets else None,
        "represented_facets": represented,
        "expected_facets": list(expected_facets),
        "subquestion_redundancy_rate": duplicate_questions / question_pairs
        if question_pairs
        else 0,
        "search_query_diversity": 1 - duplicate_queries / query_pairs if query_pairs else 1,
        "subquestion_count": len(questions),
        "search_query_count": len(queries),
    }


def valid_plan_rate(valid_runs: list[bool]) -> float | None:
    return sum(valid_runs) / len(valid_runs) if valid_runs else None


def _distribution(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


async def evaluate_job(
    session_factory: async_sessionmaker,
    job_id: str,
    *,
    expected_facets: dict[str, list[str]],
    relevant_seed_urls: set[str],
    relevant_page_urls: set[str],
) -> dict:
    async with session_factory() as session:
        job = await session.get(ResearchJob, job_id)
        if job is None or job.plan_json is None:
            raise ValueError("research job has no persisted plan")
        plan = ResearchPlan.model_validate(json.loads(job.plan_json))
        seed_rows = (
            await session.execute(
                select(ResearchSeed, ResearchSearchResult)
                .join(ResearchSearchResult, ResearchSeed.result_id == ResearchSearchResult.id)
                .where(ResearchSeed.research_job_id == job_id)
            )
        ).all()
        selected = [(seed, result) for seed, result in seed_rows if seed.selected]
        crawl_ids = {seed.crawl_job_id for seed, _ in selected if seed.crawl_job_id}
        pages = []
        if crawl_ids:
            pages = list(
                (
                    await session.execute(
                        select(CrawledPage).where(CrawledPage.crawl_job_id.in_(crawl_ids))
                    )
                ).scalars()
            )
        completed = [page for page in pages if page.crawl_status == "COMPLETED"]
        attempted = [page for page in pages if page.error_message != "max_pages reached"]
        page_scores = [
            page.actual_page_relevance
            for page in completed
            if page.actual_page_relevance is not None
        ]
        crawl_states = Counter()
        if crawl_ids:
            jobs = (
                await session.execute(select(CrawlJob).where(CrawlJob.id.in_(crawl_ids)))
            ).scalars()
            crawl_states.update(crawl.status for crawl in jobs)
        return {
            "plan": plan_metrics(plan, expected_facets),
            "seed_precision_at_k": (
                sum(result.normalized_url in relevant_seed_urls for _, result in selected)
                / len(selected)
                if selected
                else None
            ),
            "relevant_seed_yield": sum(
                result.normalized_url in relevant_seed_urls for _, result in selected
            ),
            "unique_domain_ratio": (
                len({result.domain for _, result in selected}) / len(selected) if selected else None
            ),
            "selected_semantic_relevance": _distribution(
                [seed.semantic_relevance for seed, _ in selected]
            ),
            "rejected_semantic_relevance": _distribution(
                [seed.semantic_relevance for seed, _ in seed_rows if not seed.selected]
            ),
            "relevant_page_yield": sum(
                page.normalized_url in relevant_page_urls for page in completed
            ),
            "mean_page_relevance": statistics.mean(page_scores) if page_scores else None,
            "crawl_budget_utilization": len(attempted) / job.max_total_pages,
            "pages_crawled": len(completed),
            "crawl_states": dict(crawl_states),
        }
