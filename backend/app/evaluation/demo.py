"""Run both modes against the bundled, fully mocked research site using a real model."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx
from sqlalchemy import select

from app.core.config import Settings
from app.crawler.normalizer import hostname, normalize_url
from app.db.models import CrawledPage, DiscoveredLink
from app.evaluation.compare import compare_jobs
from app.main import create_app

SITE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "research_site"
HOST = "research.example.test"
QUERY = "evaluation techniques for hallucinations in RAG systems"


class FixtureValidator:
    async def validate(self, url: str) -> str:
        normalized = normalize_url(url)
        if hostname(normalized) != HOST:
            raise ValueError("Controlled demo only permits its fixture host")
        return normalized


def fixture_response(request: httpx.Request) -> httpx.Response:
    if request.url.host != HOST:
        return httpx.Response(404)
    if request.url.path == "/robots.txt":
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="User-agent: *\nAllow: /\n",
        )
    filename = "index.html" if request.url.path == "/" else request.url.path.lstrip("/")
    target = (SITE_ROOT / filename).resolve()
    if not target.is_relative_to(SITE_ROOT) or not target.is_file():
        return httpx.Response(404)
    return httpx.Response(
        200,
        headers={"content-type": "text/html"},
        content=target.read_bytes(),
    )


async def wait_for_job(api: httpx.AsyncClient, job_id: str) -> dict:
    for _ in range(6000):
        response = (await api.get(f"/api/v1/crawl/{job_id}")).json()
        if response["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return response
        await asyncio.sleep(0.1)
    raise TimeoutError("Demo crawl did not finish")


async def run_demo(database_url: str) -> dict:
    settings = Settings(
        database_url=database_url,
        domain_delay_seconds=0,
        max_retries=0,
        request_timeout_seconds=5,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture_response)) as source:
            service = app.state.crawl_service
            service.validator = FixtureValidator()
            service.fetcher.validator = service.validator
            service.fetcher.client = source
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                jobs = {}
                statuses = {}
                for mode in ("fifo", "intelligent"):
                    payload = {
                        "start_url": f"https://{HOST}/",
                        "research_query": QUERY,
                        "crawl_mode": mode,
                        "max_depth": 1,
                        "max_pages": 4,
                        "timeout": 600,
                        "exploration_rate": 0,
                    }
                    created = await api.post("/api/v1/crawl", json=payload)
                    created.raise_for_status()
                    jobs[mode] = created.json()["job_id"]
                    statuses[mode] = await wait_for_job(api, jobs[mode])
                    if statuses[mode]["status"] != "COMPLETED":
                        raise RuntimeError(
                            f"{mode} crawl failed: {statuses[mode]['error_message']}"
                        )
                links = (
                    await api.get(
                        f"/api/v1/crawl/{jobs['intelligent']}/links",
                        params={"order": "relevance", "limit": 100},
                    )
                ).json()
                labels = set(json.loads((SITE_ROOT / "labels.json").read_text()))
                metrics = await compare_jobs(
                    service.session_factory,
                    jobs["fifo"],
                    jobs["intelligent"],
                    relevance_threshold=0.5,
                    high_threshold=0.6,
                    relevant_urls=labels,
                )
                async with service.session_factory() as session:
                    pages = list(
                        (
                            await session.execute(
                                select(CrawledPage).where(
                                    CrawledPage.crawl_job_id.in_(jobs.values())
                                )
                            )
                        ).scalars()
                    )
                    persisted_links = list(
                        (
                            await session.execute(
                                select(DiscoveredLink).where(
                                    DiscoveredLink.crawl_job_id.in_(jobs.values())
                                )
                            )
                        ).scalars()
                    )
                return {
                    "query": QUERY,
                    "jobs": jobs,
                    "statuses": statuses,
                    "metrics": {key: asdict(value) for key, value in metrics.items()},
                    "top_links": links[:8],
                    "database_inspection": {
                        "pages_with_actual_score": sum(
                            page.actual_page_relevance is not None for page in pages
                        ),
                        "links_with_prediction": sum(
                            link.relevance_score is not None for link in persisted_links
                        ),
                    },
                }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run measured FIFO and BGE-M3 fixture crawls")
    parser.add_argument("--database-url", default="sqlite+aiosqlite:///./spidermind-demo.db")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_demo(args.database_url)), indent=2))


if __name__ == "__main__":
    main()
