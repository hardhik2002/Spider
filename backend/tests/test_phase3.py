import asyncio

import httpx
import pytest
from app.core.config import Settings
from app.crawler.normalizer import is_private_address, normalize_url
from app.crawler.security import UnsafeTarget
from app.db.models import ResearchQuerySubquestion, ResearchResultOccurrence
from app.evaluation.phase3 import evaluate_job, plan_metrics, valid_plan_rate
from app.main import create_app
from app.research.search import SearchResult
from app.schemas.research import (
    PlannedSearchQuery,
    PlannedSubquestion,
    ResearchPlan,
    ResearchRequest,
    validate_plan,
)
from app.services.research_service import seed_score
from sqlalchemy import func, select


class FakePlanner:
    provider_name = "fixture"
    model_name = "fixture-plan"

    async def plan(self, request):
        return ResearchPlan(
            original_question=request.question,
            normalized_question=request.question,
            objective="Compare alpha and beta evidence",
            assumptions=[],
            scope_inclusions=["alpha", "beta"],
            scope_exclusions=[],
            time_sensitivity="evergreen",
            subquestions=[
                PlannedSubquestion(
                    id="alpha",
                    question="Investigate alpha evidence",
                    rationale="Cover alpha evidence",
                    priority="high",
                    expected_evidence=["Alpha methods"],
                    preferred_source_types=["technical documentation"],
                    search_queries=[
                        PlannedSearchQuery(query="shared evidence", intent="Broad context"),
                        PlannedSearchQuery(query="alpha evidence", intent="Alpha primary source"),
                    ],
                ),
                PlannedSubquestion(
                    id="beta",
                    question="Investigate beta evidence",
                    rationale="Cover beta evidence",
                    priority="low",
                    expected_evidence=["Beta methods"],
                    preferred_source_types=["technical documentation"],
                    search_queries=[
                        PlannedSearchQuery(query="shared evidence", intent="Broad context"),
                        PlannedSearchQuery(query="beta evidence", intent="Beta primary source"),
                    ],
                ),
            ],
        )


class TwoSeedPlanner(FakePlanner):
    async def plan(self, request):
        plan = await super().plan(request)
        return plan.model_copy(update={"subquestions": plan.subquestions[:1]})


class FakeSearch:
    provider_name = "fixture"

    def __init__(self, fail_beta=False):
        self.calls = []
        self.fail_beta = fail_beta

    async def search(self, query, limit):
        self.calls.append(query)
        if query == "beta evidence" and self.fail_beta:
            raise RuntimeError("search unavailable")
        urls = {
            "shared evidence": [
                ("Alpha evidence", "https://research.example/alpha"),
                ("Beta evidence", "https://research.example/beta"),
                ("Private", "http://127.0.0.1/secret"),
            ],
            "alpha evidence": [("Alpha evidence", "https://research.example/alpha#fragment")],
            "beta evidence": [("Beta evidence", "https://research.example/beta")],
        }[query]
        return [
            SearchResult(title, url, title, rank, query, self.provider_name)
            for rank, (title, url) in enumerate(urls[:limit], 1)
        ]


class SharedSearch(FakeSearch):
    async def search(self, query, limit):
        self.calls.append(query)
        return [
            SearchResult(
                "Alpha and beta evidence",
                "https://research.example/shared",
                "Shared methods",
                1,
                query,
                self.provider_name,
            )
        ]


class FakeEmbedding:
    model_name = "fixture-embedding"
    load_time_ms = 0

    async def embed(self, text):
        return [1.0, 0.0] if "alpha" in text.lower() else [0.0, 1.0]

    async def embed_many(self, texts):
        return [await self.embed(text) for text in texts]


class FixtureValidator:
    async def validate(self, url):
        normalized = normalize_url(url)
        if is_private_address(httpx.URL(normalized).host):
            raise UnsafeTarget("Private target blocked")
        return normalized


async def wait_research(api, job_id):
    for _ in range(300):
        status = (await api.get(f"/api/v1/research/{job_id}")).json()
        if status["status"] in {"completed", "partial", "failed"}:
            return status
        await asyncio.sleep(0.02)
    pytest.fail("Research job did not finish")


@pytest.mark.asyncio
async def test_research_pipeline_dedup_budget_and_traceability(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'research.db').as_posix()}",
        domain_delay_seconds=0,
        max_retries=0,
    )
    search = FakeSearch()
    app = create_app(
        settings,
        embedding_provider=FakeEmbedding(),
        research_planner=FakePlanner(),
        search_provider=search,
    )
    fetched = []

    def responder(request):
        fetched.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/common":
            return httpx.Response(
                200, headers={"content-type": "text/html"}, text="Common evidence"
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=f'<main>{request.url.path} <a href="/common">Common evidence</a></main>',
        )

    async with app.router.lifespan_context(app):
        service = app.state.crawl_service
        service.validator = FixtureValidator()
        service.fetcher.validator = service.validator
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as source:
            service.fetcher.client = source
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                created = await api.post(
                    "/api/v1/research",
                    json={
                        "question": "Compare alpha and beta evidence",
                        "max_subquestions": 2,
                        "search_queries_per_subquestion": 2,
                        "seeds_per_subquestion": 1,
                        "max_pages_per_subquestion": 2,
                        "max_total_pages": 4,
                        "max_depth": 1,
                    },
                )
                assert created.status_code == 202, created.text
                job_id = created.json()["research_job_id"]
                status = await wait_research(api, job_id)
                assert status["status"] == "completed", status
                assert status["total_search_queries"] == 3
                assert status["unique_search_results"] == 2
                assert status["pages_crawled"] <= 4
                assert search.calls.count("shared evidence") == 1
                assert fetched.count("https://research.example/common") == 1
                plan = (await api.get(f"/api/v1/research/{job_id}/plan")).json()
                assert len(plan["plan"]["subquestions"]) == 2
                searches = (await api.get(f"/api/v1/research/{job_id}/searches")).json()
                shared = next(
                    row for row in searches["queries"] if row["query"] == "shared evidence"
                )
                assert len(shared["subquestions"]) == 2
                sources = (await api.get(f"/api/v1/research/{job_id}/sources")).json()["sources"]
                selected = [row for row in sources if row["selected"]]
                assert {row["url"] for row in selected} == {
                    "https://research.example/alpha",
                    "https://research.example/beta",
                }
                assert all(row["crawl_job_id"] for row in selected)
                assert all(row["pages"] for row in selected)
                assert all("127.0.0.1" not in row["url"] for row in sources)
                async with service.session_factory() as session:
                    assert (
                        await session.scalar(select(func.count(ResearchQuerySubquestion.id))) == 4
                    )
                    assert (
                        await session.scalar(select(func.count(ResearchResultOccurrence.id))) == 4
                    )
                metrics = await evaluate_job(
                    service.session_factory,
                    job_id,
                    expected_facets={"alpha": ["alpha"], "beta": ["beta"]},
                    relevant_seed_urls={
                        "https://research.example/alpha",
                        "https://research.example/beta",
                    },
                    relevant_page_urls={
                        "https://research.example/alpha",
                        "https://research.example/beta",
                        "https://research.example/common",
                    },
                )
                assert metrics["plan"]["facet_coverage"] == 1
                assert metrics["seed_precision_at_k"] == 1
                assert metrics["relevant_seed_yield"] == 2
                assert metrics["relevant_page_yield"] >= 2
                assert 0 < metrics["crawl_budget_utilization"] <= 1


@pytest.mark.asyncio
async def test_search_failure_isolated_and_validation(tmp_path):
    request = ResearchRequest(question="Compare alpha and beta evidence", max_subquestions=2)
    with pytest.raises(ValueError, match="max_subquestions"):
        validate_plan(
            await FakePlanner().plan(request), request.model_copy(update={"max_subquestions": 1})
        )
    assert seed_score(1.0, 1, 0.85) == pytest.approx(1.0)
    assert seed_score(0.0, 2, 0.85) < seed_score(1.0, 5, 0.85)
    assert valid_plan_rate([True, False, True]) == pytest.approx(2 / 3)
    assert (
        plan_metrics(await FakePlanner().plan(request), {"alpha": ["alpha"]})["facet_coverage"] == 1
    )
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'failure.db').as_posix()}",
        domain_delay_seconds=0,
        max_retries=0,
    )
    app = create_app(
        settings,
        embedding_provider=FakeEmbedding(),
        research_planner=FakePlanner(),
        search_provider=FakeSearch(fail_beta=True),
    )
    async with app.router.lifespan_context(app):
        service = app.state.crawl_service
        service.validator = FixtureValidator()
        service.fetcher.validator = service.validator
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(404)
                    if request.url.path == "/robots.txt"
                    else httpx.Response(200, headers={"content-type": "text/html"}, text="Evidence")
                )
            )
        ) as source:
            service.fetcher.client = source
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                created = await api.post(
                    "/api/v1/research",
                    json={
                        "question": request.question,
                        "max_subquestions": 2,
                        "search_queries_per_subquestion": 2,
                        "max_depth": 0,
                    },
                )
                status = await wait_research(api, created.json()["research_job_id"])
                assert status["status"] in {"completed", "partial"}, status
                searches = (
                    await api.get(f"/api/v1/research/{created.json()['research_job_id']}/searches")
                ).json()
                assert any(row["status"] == "failed" for row in searches["queries"])
                assert any(row["status"] == "completed" for row in searches["queries"])


@pytest.mark.asyncio
async def test_global_budget_prioritizes_high_subquestion(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'budget.db').as_posix()}",
        domain_delay_seconds=0,
    )
    app = create_app(
        settings,
        embedding_provider=FakeEmbedding(),
        research_planner=FakePlanner(),
        search_provider=FakeSearch(),
    )
    async with app.router.lifespan_context(app):
        service = app.state.crawl_service
        service.validator = FixtureValidator()
        service.fetcher.validator = service.validator
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(404)
                    if request.url.path == "/robots.txt"
                    else httpx.Response(
                        200, headers={"content-type": "text/html"}, text="Evidence page"
                    )
                )
            )
        ) as source:
            service.fetcher.client = source
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                invalid = await api.post(
                    "/api/v1/research",
                    json={"question": "Compare alpha and beta", "max_total_pages": 10000},
                )
                assert invalid.status_code == 422
                created = await api.post(
                    "/api/v1/research",
                    json={
                        "question": "Compare alpha and beta evidence",
                        "max_subquestions": 2,
                        "search_queries_per_subquestion": 2,
                        "seeds_per_subquestion": 1,
                        "max_total_pages": 1,
                        "max_pages_per_subquestion": 1,
                        "max_depth": 0,
                    },
                )
                job_id = created.json()["research_job_id"]
                status = await wait_research(api, job_id)
                assert status["status"] == "partial", status
                assert status["pages_crawled"] == 1
                assert status["selected_seed_count"] == 1
                sources = (await api.get(f"/api/v1/research/{job_id}/sources")).json()["sources"]
                alpha = next(
                    row for row in sources if row["subquestion_id"] == "alpha" and row["selected"]
                )
                beta = next(
                    row
                    for row in sources
                    if row["subquestion_id"] == "beta" and row["rejection_reason"] == "PAGE_BUDGET"
                )
                assert alpha["crawl_job_id"]
                assert beta["crawl_job_id"] is None


@pytest.mark.asyncio
async def test_failed_seed_page_marks_research_partial(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'page-failure.db').as_posix()}",
        domain_delay_seconds=0,
        max_retries=0,
    )
    app = create_app(
        settings,
        embedding_provider=FakeEmbedding(),
        research_planner=FakePlanner(),
        search_provider=FakeSearch(),
    )
    async with app.router.lifespan_context(app):
        service = app.state.crawl_service
        service.validator = FixtureValidator()
        service.fetcher.validator = service.validator
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(404)
                    if request.url.path == "/robots.txt"
                    else httpx.Response(503)
                    if request.url.path == "/alpha"
                    else httpx.Response(
                        200, headers={"content-type": "text/html"}, text="Beta evidence"
                    )
                )
            )
        ) as source:
            service.fetcher.client = source
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                created = await api.post(
                    "/api/v1/research",
                    json={
                        "question": "Compare alpha and beta evidence",
                        "max_subquestions": 2,
                        "search_queries_per_subquestion": 2,
                        "seeds_per_subquestion": 1,
                        "max_total_pages": 2,
                        "max_pages_per_subquestion": 1,
                        "max_depth": 0,
                    },
                )
                status = await wait_research(api, created.json()["research_job_id"])
                assert status["status"] == "partial", status
                assert status["failed_subquestions"] == 1
                assert status["pages_crawled"] == 1


@pytest.mark.asyncio
async def test_multiple_seeds_share_subquestion_budget(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'multi-seed.db').as_posix()}",
        domain_delay_seconds=0,
        max_seeds_per_domain_per_subquestion=2,
    )
    app = create_app(
        settings,
        embedding_provider=FakeEmbedding(),
        research_planner=TwoSeedPlanner(),
        search_provider=FakeSearch(),
    )
    async with app.router.lifespan_context(app):
        service = app.state.crawl_service
        service.validator = FixtureValidator()
        service.fetcher.validator = service.validator
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(404)
                    if request.url.path == "/robots.txt"
                    else httpx.Response(
                        200, headers={"content-type": "text/html"}, text=str(request.url.path)
                    )
                )
            )
        ) as source:
            service.fetcher.client = source
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                created = await api.post(
                    "/api/v1/research",
                    json={
                        "question": "Compare alpha and beta evidence",
                        "max_subquestions": 1,
                        "search_queries_per_subquestion": 2,
                        "seeds_per_subquestion": 2,
                        "max_pages_per_subquestion": 2,
                        "max_total_pages": 2,
                        "max_depth": 0,
                    },
                )
                job_id = created.json()["research_job_id"]
                status = await wait_research(api, job_id)
                assert status["status"] == "completed", status
                assert status["selected_seed_count"] == 2
                assert status["pages_crawled"] == 2
                sources = (await api.get(f"/api/v1/research/{job_id}/sources")).json()["sources"]
                assert len({row["crawl_job_id"] for row in sources if row["selected"]}) == 2


@pytest.mark.asyncio
async def test_shared_seed_crawled_once_with_two_subquestion_links(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'shared.db').as_posix()}",
        domain_delay_seconds=0,
    )
    app = create_app(
        settings,
        embedding_provider=FakeEmbedding(),
        research_planner=FakePlanner(),
        search_provider=SharedSearch(),
    )
    fetched = []

    def responder(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        fetched.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text="Shared evidence")

    async with app.router.lifespan_context(app):
        service = app.state.crawl_service
        service.validator = FixtureValidator()
        service.fetcher.validator = service.validator
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as source:
            service.fetcher.client = source
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                created = await api.post(
                    "/api/v1/research",
                    json={
                        "question": "Compare alpha and beta evidence",
                        "max_subquestions": 2,
                        "search_queries_per_subquestion": 2,
                        "seeds_per_subquestion": 1,
                        "max_pages_per_subquestion": 1,
                        "max_total_pages": 2,
                        "max_depth": 0,
                    },
                )
                job_id = created.json()["research_job_id"]
                status = await wait_research(api, job_id)
                assert status["status"] == "completed", status
                assert status["pages_crawled"] == 1
                sources = (await api.get(f"/api/v1/research/{job_id}/sources")).json()["sources"]
                assert len(sources) == 2
                assert len({row["crawl_job_id"] for row in sources}) == 1
                assert fetched == ["https://research.example/shared"]
