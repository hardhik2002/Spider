import asyncio
import socket

import httpx
import pytest
from app.crawler.deduplicator import ContentDeduplicator, content_hash
from app.crawler.fetcher import Fetcher, FetchError
from app.crawler.frontier import Frontier
from app.crawler.models import FrontierItem
from app.crawler.normalizer import InvalidURL, normalize_url
from app.crawler.parser import parse_page
from app.crawler.rate_limit import DomainRateLimiter
from app.crawler.security import TargetValidator, UnsafeTarget


@pytest.mark.parametrize(
    ("url", "base", "expected"),
    [
        ("HTTPS://Example.COM:443/a///b/#frag", None, "https://example.com/a/b"),
        ("/about#part", "https://example.com/x", "https://example.com/about"),
        ("https://example.com:80/", None, "https://example.com:80/"),
        ("http://EXAMPLE.com:80/a/", None, "http://example.com/a"),
    ],
)
def test_normalize(url, base, expected):
    assert normalize_url(url, base) == expected


@pytest.mark.parametrize(
    "url",
    [
        "mailto:a@b.com",
        "tel:123",
        "javascript:alert(1)",
        "http://",
        "ftp://example.com",
        "https://u:p@example.com",
    ],
)
def test_invalid_url(url):
    with pytest.raises(InvalidURL):
        normalize_url(url)


def test_frontier_and_content_dedup():
    frontier = Frontier()
    item = FrontierItem("https://example.com/", "https://example.com/", 0)
    assert frontier.push(item)
    assert not frontier.push(item)
    assert frontier.pop() == item
    assert not frontier
    dedup = ContentDeduplicator()
    digest = content_hash("HELLO   world")
    assert digest == content_hash("hello world")
    assert dedup.find_or_add(digest, 1) is None
    assert dedup.find_or_add(digest, 2) == 1


def test_parser_extracts_content_metadata_and_links():
    html = b"""<html><head><title>Example</title><meta name="description" content="Summary">
    <link rel="canonical" href="/canonical"></head><body><nav>Menu junk</nav>
    <main><h1>Research</h1><p>This is the useful research text.</p>
    <a href="/inside">Inside</a><a href="https://outside.test/x">Outside</a></main></body></html>"""
    page = parse_page(html, "https://example.com/", "example.com")
    assert page.title == "Example"
    assert page.meta_description == "Summary"
    assert page.canonical_url == "https://example.com/canonical"
    assert "Research" in page.text_content
    assert "Menu junk" not in page.text_content
    assert [link.is_internal for link in page.links] == [True, False]


@pytest.mark.asyncio
async def test_private_ips_and_dns_rebinding(monkeypatch):
    validator = TargetValidator()
    for url in [
        "http://localhost",
        "http://127.1",
        "http://10.0.0.1",
        "http://[::1]",
        "http://169.254.169.254",
    ]:
        with pytest.raises(UnsafeTarget):
            await validator.validate(url)

    loop = asyncio.get_running_loop()

    async def private_dns(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.4", 80))]

    monkeypatch.setattr(loop, "getaddrinfo", private_dns)
    with pytest.raises(UnsafeTarget):
        await validator.validate("https://public.example/")


class PublicValidator:
    async def validate(self, url):
        return normalize_url(url)


@pytest.mark.asyncio
async def test_fetcher_rejects_content_type_size_and_permanent_error():
    hits = []

    def responder(request):
        hits.append(str(request.url))
        if request.url.path == "/pdf":
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"pdf")
        if request.url.path == "/large":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 100)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        fetcher = Fetcher(
            client,
            PublicValidator(),
            DomainRateLimiter(0),
            timeout=1,
            max_redirects=1,
            max_retries=2,
        )
        for path in ["pdf", "large", "missing"]:
            with pytest.raises(FetchError):
                await fetcher.fetch("https://example.com/" + path, 20)
    assert hits.count("https://example.com/missing") == 1


@pytest.mark.asyncio
async def test_redirect_to_private_target_is_blocked():
    async def responder(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        fetcher = Fetcher(
            client,
            TargetValidator(),
            DomainRateLimiter(0),
            timeout=1,
            max_redirects=2,
            max_retries=0,
        )
        # Avoid a real DNS lookup only for the initial host.
        original = fetcher.validator.validate

        async def first_public(url):
            if url == "https://example.com/":
                return url
            return await original(url)

        fetcher.validator.validate = first_public
        with pytest.raises(FetchError, match="blocked"):
            await fetcher.fetch("https://example.com/", 100)
