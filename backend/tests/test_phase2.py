import asyncio
import json
import sqlite3

import httpx
import pytest
from app.core.config import Settings
from app.crawler.frontier import Frontier, PriorityFrontier
from app.crawler.models import FrontierItem
from app.crawler.scoring import (
    LinkCandidate,
    SemanticScorer,
    candidate_representation,
    cosine_similarity,
)
from app.db.database import initialize_database, make_engine, make_session_factory
from app.db.models import CrawledPage, CrawlJob, DiscoveredLink
from app.evaluation.compare import compare_jobs
from app.evaluation.demo import HOST, QUERY, SITE_ROOT, fixture_response
from app.main import create_app
from app.schemas.crawl import CrawlRequest
from pydantic import ValidationError
from sqlalchemy import select


class CountingProvider:
    model_name = "deterministic-test-provider"
    load_time_ms = 0

    def __init__(self):
        self.query_calls = 0
        self.batch_sizes = []

    def vector(self, text):
        if text == QUERY:
            return [1.0, 0.0]
        lowered = text.lower()
        if "hallucination" in lowered or "rag" in lowered:
            return [0.95, 0.05]
        return [0.0, 1.0]

    async def embed(self, text):
        if text == QUERY:
            self.query_calls += 1
        return self.vector(text)

    async def embed_many(self, texts):
        self.batch_sizes.append(len(texts))
        return [self.vector(text) for text in texts]


class FixtureValidator:
    async def validate(self, url):
        return url


def test_mode_validation_and_fifo_default():
    assert CrawlRequest(start_url="https://example.com").crawl_mode == "fifo"
    with pytest.raises(ValidationError, match="research_query is required"):
        CrawlRequest(start_url="https://example.com", crawl_mode="intelligent")
    with pytest.raises(ValidationError):
        CrawlRequest(start_url="https://example.com", crawl_mode="intelligent", research_query=" ")
    assert (
        CrawlRequest(
            start_url="https://example.com", crawl_mode="intelligent", research_query="  RAG  "
        ).research_query
        == "RAG"
    )


@pytest.mark.asyncio
async def test_provider_batch_cache_and_cosine():
    provider = CountingProvider()
    scorer = SemanticScorer(provider, depth_penalty=0.02, max_context_chars=30, max_page_chars=100)
    await scorer.prepare(QUERY)
    await scorer.prepare(QUERY)
    candidates = [
        LinkCandidate("https://example.com/rag", "RAG", "Research", "Some context", 1),
        LinkCandidate("https://example.com/about", "About", "Research", "Other", 2),
    ]
    first = await scorer.score_batch(candidates)
    second = await scorer.score_batch(candidates)
    assert provider.query_calls == 1
    assert provider.batch_sizes == [2]
    assert first == second
    assert first[0].relevance_score > first[1].relevance_score
    assert first[0].priority_score == pytest.approx(first[0].relevance_score - 0.02)
    assert first[1].depth_penalty == 0.04
    assert cosine_similarity([1, 0], [0, 1]) == 0
    assert cosine_similarity([1, 0], [1, 0]) == 1
    with pytest.raises(ValueError):
        cosine_similarity([1], [1, 0])
    text = candidate_representation(candidates[0], 5)
    assert "Target URL:" in text and "Anchor: RAG" in text
    assert "Source page: Research" in text and "Context: Some" in text


def test_priority_ties_depth_and_exploration():
    frontier = PriorityFrontier(0)
    items = [
        FrontierItem("a", "a", 2, priority_score=0.7, discovery_order=1),
        FrontierItem("b", "b", 1, priority_score=0.8, discovery_order=3),
        FrontierItem("c", "c", 1, priority_score=0.8, discovery_order=2),
        FrontierItem("d", "d", 0, priority_score=0.9, discovery_order=4),
    ]
    for item in items:
        assert frontier.push(item)
    assert not frontier.push(items[0])
    assert [frontier.pop().url for _ in range(4)] == ["d", "c", "b", "a"]
    fifo = Frontier()
    for item in items:
        fifo.push(item)
    assert [fifo.pop().url for _ in range(4)] == ["a", "b", "c", "d"]

    exploring = PriorityFrontier(0.5)
    for item in items:
        exploring.push(item)
    assert exploring.pop().url == "d"
    assert exploring.pop().url == "a"
    assert exploring.last_pop_exploration is True


async def wait_for_job(api, job_id):
    for _ in range(500):
        response = (await api.get(f"/api/v1/crawl/{job_id}")).json()
        if response["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return response
        await asyncio.sleep(0.01)
    pytest.fail("Crawler did not finish")


@pytest.mark.asyncio
async def test_intelligent_order_persistence_links_threshold_and_metrics(tmp_path):
    provider = CountingProvider()
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'phase2.db').as_posix()}",
        domain_delay_seconds=0,
        max_retries=0,
    )
    app = create_app(settings, embedding_provider=provider)
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
                for mode in ("fifo", "intelligent"):
                    created = await api.post(
                        "/api/v1/crawl",
                        json={
                            "start_url": f"https://{HOST}/",
                            "research_query": QUERY,
                            "crawl_mode": mode,
                            "max_depth": 1,
                            "max_pages": 4,
                            "exploration_rate": 0,
                        },
                    )
                    assert created.status_code == 202, created.text
                    jobs[mode] = created.json()["job_id"]
                    status = await wait_for_job(api, jobs[mode])
                    assert status["status"] == "COMPLETED", status
                intelligent = (await api.get(f"/api/v1/crawl/{jobs['intelligent']}")).json()
                assert intelligent["links_scored"] == 8
                assert intelligent["candidate_embeddings"] == 8
                assert intelligent["embedding_model"] == provider.model_name
                links = (
                    await api.get(
                        f"/api/v1/crawl/{jobs['intelligent']}/links",
                        params={"order": "relevance", "limit": 100},
                    )
                ).json()
                assert len(links) == 8
                assert links[0]["relevance_score"] >= links[-1]["relevance_score"]
                assert all(link["link_context"] for link in links)
                assert all(link["source_page_title"] for link in links)
                assert all(link["priority_score"] is not None for link in links)
                selected = (
                    await api.get(
                        f"/api/v1/crawl/{jobs['intelligent']}/links",
                        params={"selection": "selected"},
                    )
                ).json()
                assert len(selected) == 3
                assert all(link["is_internal"] for link in selected)
                async with service.session_factory() as session:
                    pages = list((await session.execute(select(CrawledPage))).scalars())
                    stored_links = list((await session.execute(select(DiscoveredLink))).scalars())
                    assert any(page.actual_page_relevance is not None for page in pages)
                    assert any(page.predicted_link_relevance is not None for page in pages)
                    assert any(link.rejection_reason == "PAGE_LIMIT" for link in stored_links)
                labels = set(json.loads((SITE_ROOT / "labels.json").read_text()))
                metrics = await compare_jobs(
                    service.session_factory,
                    jobs["fifo"],
                    jobs["intelligent"],
                    relevance_threshold=0.5,
                    high_threshold=0.6,
                    relevant_urls=labels,
                )
                assert metrics["fifo"].fetched_order[1].endswith("/company/about.html")
                assert all("/ai/" in url for url in metrics["intelligent"].fetched_order[1:])
                assert metrics["intelligent"].relevant_pages == 3
                assert metrics["fifo"].relevant_pages == 1
                assert metrics["intelligent"].crawl_efficiency == 0.75
                assert provider.query_calls == 2  # once per job

                threshold_created = await api.post(
                    "/api/v1/crawl",
                    json={
                        "start_url": f"https://{HOST}/",
                        "research_query": QUERY,
                        "crawl_mode": "intelligent",
                        "max_depth": 1,
                        "max_pages": 10,
                        "min_relevance_score": 0.5,
                    },
                )
                threshold_id = threshold_created.json()["job_id"]
                threshold_status = await wait_for_job(api, threshold_id)
                assert threshold_status["status"] == "COMPLETED"
                assert threshold_status["links_below_threshold"] >= 4
                rejected = (
                    await api.get(
                        f"/api/v1/crawl/{threshold_id}/links",
                        params={"selection": "rejected", "limit": 100},
                    )
                ).json()
                assert any(row["rejection_reason"] == "LOW_RELEVANCE" for row in rejected)


@pytest.mark.asyncio
async def test_existing_phase1_sqlite_migrates_additively(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE crawl_jobs (
        id TEXT PRIMARY KEY, start_url TEXT NOT NULL, status TEXT NOT NULL,
        created_at DATETIME NOT NULL, started_at DATETIME, completed_at DATETIME,
        max_depth INTEGER NOT NULL, max_pages INTEGER NOT NULL, timeout FLOAT NOT NULL,
        max_content_size INTEGER NOT NULL, allowed_domains TEXT NOT NULL,
        allow_external_domains BOOLEAN NOT NULL, pages_discovered INTEGER NOT NULL,
        pages_crawled INTEGER NOT NULL, pages_failed INTEGER NOT NULL,
        pages_skipped INTEGER NOT NULL, error_message TEXT
        )"""
    )
    for table in ("crawled_pages", "discovered_links"):
        connection.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
    connection.execute(
        """INSERT INTO crawl_jobs VALUES (
        'old-job', 'https://example.com/', 'COMPLETED', '2026-01-01 00:00:00',
        NULL, NULL, 2, 25, 120.0, 2000000, '[]', 0, 3, 2, 0, 1, NULL
        )"""
    )
    connection.commit()
    connection.close()
    engine = make_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    try:
        await initialize_database(engine)
        await initialize_database(engine)
        async with engine.connect() as connection:
            rows = await connection.exec_driver_sql("PRAGMA table_info(crawl_jobs)")
            names = [row[1] for row in rows]
            assert names.count("research_query") == 1
            assert names.count("crawl_mode") == 1
            old = await connection.exec_driver_sql("SELECT id, crawl_mode FROM crawl_jobs")
            assert old.first() == ("old-job", "fifo")
        async with make_session_factory(engine)() as session:
            old_job = await session.get(CrawlJob, "old-job")
            assert old_job.pages_crawled == 2
            assert old_job.research_query is None
            assert old_job.crawl_mode == "fifo"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_private_discovered_link_is_rejected(tmp_path):
    provider = CountingProvider()
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'private.db').as_posix()}",
        domain_delay_seconds=0,
    )
    app = create_app(settings, embedding_provider=provider)

    def responder(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<main>Root <a href="http://127.0.0.1/private">Private</a></main>',
        )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as source:
            service = app.state.crawl_service
            service.validator = FixtureValidator()
            service.fetcher.validator = service.validator
            service.fetcher.client = source
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                created = await api.post(
                    "/api/v1/crawl",
                    json={
                        "start_url": f"https://{HOST}/",
                        "research_query": QUERY,
                        "crawl_mode": "intelligent",
                        "allow_external_domains": True,
                    },
                )
                assert created.status_code == 202
                status = await wait_for_job(api, created.json()["job_id"])
                assert status["status"] == "COMPLETED"
                links = (await api.get(f"/api/v1/crawl/{created.json()['job_id']}/links")).json()
                assert links[0]["rejection_reason"] == "SSRF_BLOCKED"
                assert links[0]["selected_for_crawl"] is False


@pytest.mark.asyncio
async def test_intelligent_external_scope_and_robots(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'scope.db').as_posix()}",
        domain_delay_seconds=0,
    )
    app = create_app(settings, embedding_provider=CountingProvider())
    hits = []

    def responder(request):
        hits.append(str(request.url))
        if request.url.path == "/robots.txt":
            text = "User-agent: *\nDisallow: /blocked\n" if request.url.host == HOST else ""
            return httpx.Response(200, headers={"content-type": "text/plain"}, text=text)
        if request.url.host == HOST and request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<main>Root <a href="/blocked">Blocked RAG page</a>'
                    '<a href="https://external.test/research">External RAG</a></main>'
                ),
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<main>External research content</main>",
        )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as source:
            service = app.state.crawl_service
            service.validator = FixtureValidator()
            service.fetcher.validator = service.validator
            service.fetcher.client = source
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                for allow_external in (False, True):
                    created = await api.post(
                        "/api/v1/crawl",
                        json={
                            "start_url": f"https://{HOST}/",
                            "crawl_mode": "intelligent",
                            "research_query": QUERY,
                            "allow_external_domains": allow_external,
                            "max_pages": 3,
                        },
                    )
                    assert created.status_code == 202
                    job_id = created.json()["job_id"]
                    status = await wait_for_job(api, job_id)
                    assert status["status"] == "COMPLETED"
                    links = (await api.get(f"/api/v1/crawl/{job_id}/links")).json()
                    external_only = (
                        await api.get(
                            f"/api/v1/crawl/{job_id}/links",
                            params={"internal": "false", "limit": 1, "offset": 0},
                        )
                    ).json()
                    assert len(external_only) == 1
                    assert external_only[0]["is_internal"] is False
                    by_url = {row["normalized_url"]: row for row in links}
                    assert by_url[f"https://{HOST}/blocked"]["rejection_reason"] == "ROBOTS_DENIED"
                    external = by_url["https://external.test/research"]
                    if allow_external:
                        assert external["scoring_status"] == "SCORED"
                        assert external["selected_for_crawl"] is True
                        assert status["pages_crawled"] == 2
                    else:
                        assert external["rejection_reason"] == "EXTERNAL_DOMAIN_DISABLED"
                        assert external["selected_for_crawl"] is False
                        assert status["pages_crawled"] == 1
                assert hits.count("https://external.test/research") == 1
