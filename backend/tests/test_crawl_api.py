import asyncio
from pathlib import Path

import httpx
import pytest
from app.core.config import Settings
from app.crawler.normalizer import normalize_url
from app.db.models import CrawledPage, DiscoveredLink
from app.main import create_app
from sqlalchemy import select


class PublicValidator:
    async def validate(self, url):
        return normalize_url(url)


async def run_crawl(tmp_path: Path, pages: dict[str, httpx.Response], payload: dict):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        domain_delay_seconds=0,
        max_retries=0,
        request_timeout_seconds=1,
    )
    app = create_app(settings)
    hits = []

    def responder(request):
        hits.append(str(request.url))
        return pages.get(str(request.url), httpx.Response(404))

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as source_client:
            app.state.crawl_service.client = source_client
            app.state.crawl_service.validator = PublicValidator()
            app.state.crawl_service.fetcher.client = source_client
            app.state.crawl_service.fetcher.validator = app.state.crawl_service.validator
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                created = await api.post("/api/v1/crawl", json=payload)
                assert created.status_code == 202, created.text
                job_id = created.json()["job_id"]
                for _ in range(200):
                    result = await api.get(f"/api/v1/crawl/{job_id}")
                    if result.json()["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                        break
                    await asyncio.sleep(0.01)
                else:
                    pytest.fail("Crawl did not terminate")
                async with app.state.crawl_service.session_factory() as session:
                    rows = (await session.execute(select(CrawledPage))).scalars().all()
                    links = (await session.execute(select(DiscoveredLink))).scalars().all()
                    rows_data = [
                        (
                            r.normalized_url,
                            r.crawl_status,
                            r.content_hash,
                            r.duplicate_of_page_id,
                            r.text_content,
                        )
                        for r in rows
                    ]
                    links_data = [(r.normalized_target_url, r.is_internal) for r in links]
                return result.json(), rows_data, links_data, hits


def html(body: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html"}, text=body)


def robots(body: str = "User-agent: *\nAllow: /\n") -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/plain"}, text=body)


@pytest.mark.asyncio
async def test_depth_scope_links_failures_duplicates_and_persistence(tmp_path):
    pages = {
        "https://example.com/robots.txt": robots(),
        "https://example.com/": html(
            '<main>Root unique content <a href="/a">A</a>'
            '<a href="/a#fragment">again</a>'
            '<a href="https://external.test/">ext</a>'
            '<a href="mailto:x@y">bad</a>'
            '<a href="/broken">broken</a>'
            '<a href="/copy">copy</a></main>'
        ),
        "https://example.com/a": html(
            '<main>Child useful content <a href="/deep">Deep</a><a href="/copy">Copy</a></main>'
        ),
        "https://example.com/copy": html(
            '<main>Child useful content <a href="/deep">Deep</a><a href="/copy">Copy</a></main>'
        ),
        "https://example.com/broken": httpx.Response(500),
    }
    result, rows, links, hits = await run_crawl(
        tmp_path, pages, {"start_url": "https://example.com", "max_depth": 1, "max_pages": 10}
    )
    assert result["status"] == "COMPLETED"
    assert result["pages_discovered"] == 6  # root, a, external, broken, deep, copy
    assert result["pages_failed"] == 1
    assert result["pages_skipped"] >= 3  # external, deep, duplicate content
    assert any(url == "https://external.test/" and not internal for url, internal in links)
    assert "https://external.test/" not in hits
    assert "https://example.com/deep" not in hits
    assert any(duplicate_id is not None and text is None for _, _, _, duplicate_id, text in rows)
    assert all(
        digest for _, status, digest, _, _ in rows if status in {"COMPLETED", "SKIPPED"} and digest
    )


@pytest.mark.asyncio
async def test_max_pages_terminates_and_marks_pending(tmp_path):
    pages = {
        "https://example.com/robots.txt": robots(),
        "https://example.com/": html('<main>root text <a href="/a">a</a><a href="/b">b</a></main>'),
        "https://example.com/a": html("<main>a text</main>"),
        "https://example.com/b": html("<main>b text</main>"),
    }
    result, rows, _, hits = await run_crawl(
        tmp_path, pages, {"start_url": "https://example.com", "max_pages": 1}
    )
    assert result["status"] == "COMPLETED"
    assert result["pages_crawled"] == 1
    assert result["pages_skipped"] == 2
    assert len(rows) == 3
    assert "https://example.com/a" not in hits


@pytest.mark.asyncio
async def test_robots_denial_is_skipped(tmp_path):
    pages = {"https://example.com/robots.txt": robots("User-agent: *\nDisallow: /\n")}
    result, rows, _, hits = await run_crawl(tmp_path, pages, {"start_url": "https://example.com"})
    assert result["status"] == "COMPLETED"
    assert result["pages_skipped"] == 1
    assert rows[0][1] == "SKIPPED"
    assert "https://example.com/" not in hits


@pytest.mark.asyncio
async def test_external_crawl_requires_explicit_opt_in(tmp_path):
    pages = {
        "https://example.com/robots.txt": robots(),
        "https://external.test/robots.txt": robots(),
        "https://example.com/": html('<main>Start <a href="https://external.test/">Next</a></main>'),
        "https://external.test/": html("<main>External useful content</main>"),
    }
    result, rows, links, hits = await run_crawl(
        tmp_path,
        pages,
        {
            "start_url": "https://example.com",
            "max_depth": 1,
            "allow_external_domains": True,
        },
    )
    assert result["pages_crawled"] == 2
    assert "https://external.test/" in hits
    assert len(rows) == 2
    assert links == [("https://external.test/", False)]


@pytest.mark.asyncio
async def test_redirect_out_of_scope_is_skipped(tmp_path):
    pages = {
        "https://example.com/robots.txt": robots(),
        "https://example.com/": httpx.Response(
            302, headers={"location": "https://external.test/secret"}
        ),
    }
    result, rows, _, hits = await run_crawl(
        tmp_path, pages, {"start_url": "https://example.com"}
    )
    assert result["status"] == "COMPLETED"
    assert result["pages_skipped"] == 1
    assert rows[0][1] == "SKIPPED"
    assert "https://external.test/secret" not in hits


@pytest.mark.asyncio
async def test_api_health_ready_and_invalid_start(tmp_path):
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}")
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as api:
            assert (await api.get("/health")).json() == {"status": "ok"}
            assert (await api.get("/ready")).json() == {"status": "ok"}
            assert (
                await api.post("/api/v1/crawl", json={"start_url": "mailto:a@b"})
            ).status_code == 422
            assert (
                await api.post("/api/v1/crawl", json={"start_url": "http://127.0.0.1"})
            ).status_code == 422
