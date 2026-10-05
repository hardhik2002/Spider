"""Measured FIFO versus intelligent crawl quality at the same page budget."""

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.crawler.models import CrawlMode, JobStatus, PageStatus
from app.db.database import make_engine, make_session_factory
from app.db.models import CrawledPage, CrawlJob


@dataclass(frozen=True)
class CrawlMetrics:
    job_id: str
    crawl_mode: str
    max_pages: int
    pages_crawled: int
    relevant_pages: int
    relevance_source: str
    relevant_page_yield: float
    mean_page_relevance: float
    high_relevance_hit_rate: float
    crawl_efficiency: float
    fetched_order: list[str]


async def evaluate_job(
    session_factory: async_sessionmaker,
    job_id: str,
    *,
    relevance_threshold: float,
    high_threshold: float,
    relevant_urls: set[str] | None = None,
) -> CrawlMetrics:
    async with session_factory() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None:
            raise ValueError(f"Unknown crawl job: {job_id}")
        if job.status != JobStatus.COMPLETED or not job.research_query:
            raise ValueError("Evaluation requires a completed crawl with a research query")
        pages = list(
            (
                await session.execute(
                    select(CrawledPage)
                    .where(
                        CrawledPage.crawl_job_id == job_id,
                        CrawledPage.crawl_status == PageStatus.COMPLETED,
                    )
                    .order_by(CrawledPage.id)
                )
            ).scalars()
        )
        if any(page.actual_page_relevance is None for page in pages):
            raise ValueError("Page relevance is missing for this crawl")
        scores = [page.actual_page_relevance for page in pages]
        count = len(scores)
        relevant = (
            sum(page.normalized_url in relevant_urls for page in pages)
            if relevant_urls is not None
            else sum(score >= relevance_threshold for score in scores)
        )
        high = sum(score >= high_threshold for score in scores)
        return CrawlMetrics(
            job_id=job.id,
            crawl_mode=job.crawl_mode,
            max_pages=job.max_pages,
            pages_crawled=count,
            relevant_pages=relevant,
            relevance_source=("fixture_labels" if relevant_urls is not None else "page_score"),
            relevant_page_yield=relevant / count if count else 0.0,
            mean_page_relevance=sum(scores) / count if count else 0.0,
            high_relevance_hit_rate=high / count if count else 0.0,
            crawl_efficiency=relevant / job.max_pages,
            fetched_order=[page.normalized_url for page in pages],
        )


async def compare_jobs(
    session_factory: async_sessionmaker,
    fifo_job_id: str,
    intelligent_job_id: str,
    *,
    relevance_threshold: float,
    high_threshold: float,
    relevant_urls: set[str] | None = None,
) -> dict[str, CrawlMetrics]:
    async with session_factory() as session:
        fifo = await session.get(CrawlJob, fifo_job_id)
        intelligent = await session.get(CrawlJob, intelligent_job_id)
        if not fifo or not intelligent:
            raise ValueError("Both crawl jobs must exist")
        if fifo.crawl_mode != CrawlMode.FIFO or intelligent.crawl_mode != CrawlMode.INTELLIGENT:
            raise ValueError("Expected one FIFO and one intelligent crawl")
        if (
            fifo.research_query != intelligent.research_query
            or fifo.max_pages != intelligent.max_pages
            or fifo.start_url != intelligent.start_url
            or fifo.max_depth != intelligent.max_depth
            or fifo.allow_external_domains != intelligent.allow_external_domains
            or fifo.allowed_domains != intelligent.allowed_domains
        ):
            raise ValueError("Comparison requires the same query, site, scope, and budget")
    return {
        "fifo": await evaluate_job(
            session_factory,
            fifo_job_id,
            relevance_threshold=relevance_threshold,
            high_threshold=high_threshold,
            relevant_urls=relevant_urls,
        ),
        "intelligent": await evaluate_job(
            session_factory,
            intelligent_job_id,
            relevance_threshold=relevance_threshold,
            high_threshold=high_threshold,
            relevant_urls=relevant_urls,
        ),
    }


async def _cli(args: argparse.Namespace) -> None:
    engine = make_engine(args.database_url)
    try:
        relevant_urls = set(json.loads(args.labels_file.read_text())) if args.labels_file else None
        metrics = await compare_jobs(
            make_session_factory(engine),
            args.fifo_job,
            args.intelligent_job,
            relevance_threshold=args.relevance_threshold,
            high_threshold=args.high_threshold,
            relevant_urls=relevant_urls,
        )
        print(json.dumps({name: asdict(value) for name, value in metrics.items()}, indent=2))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two completed SpiderMind crawl jobs")
    parser.add_argument("--database-url", default="sqlite+aiosqlite:///./spidermind.db")
    parser.add_argument("--fifo-job", required=True)
    parser.add_argument("--intelligent-job", required=True)
    parser.add_argument("--relevance-threshold", type=float, default=0.5)
    parser.add_argument("--high-threshold", type=float, default=0.6)
    parser.add_argument("--labels-file", type=Path)
    asyncio.run(_cli(parser.parse_args()))


if __name__ == "__main__":
    main()
