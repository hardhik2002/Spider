import json
import sys
from types import SimpleNamespace

import httpx
import pytest
from app.core.config import Settings
from app.crawler.normalizer import InvalidURL, normalize_url
from app.crawler.security import TargetValidator, UnsafeTarget
from app.research.planner import OllamaResearchPlanner
from app.research.search import DDGSSearchProvider, SearchResult
from app.schemas.research import ResearchRequest
from pydantic import ValidationError


def valid_plan(question):
    return {
        "original_question": question,
        "normalized_question": question,
        "objective": "Compare the documented evidence for alpha and beta",
        "assumptions": [],
        "scope_inclusions": ["alpha", "beta"],
        "scope_exclusions": [],
        "time_sensitivity": "evergreen",
        "subquestions": [
            {
                "id": "s1",
                "question": "What is the alpha evidence?",
                "rationale": "Find alpha evidence",
                "priority": "high",
                "expected_evidence": ["alpha methods"],
                "preferred_source_types": ["documentation"],
                "search_queries": [{"query": "alpha evidence", "intent": "Find primary sources"}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_ollama_schema_retry_and_question_only_boundary(monkeypatch):
    import app.research.planner as planner_module

    question = "Compare alpha and beta evidence"
    request = ResearchRequest(question=question, max_subquestions=1)
    calls = []
    original_client = httpx.AsyncClient

    def responder(http_request):
        payload = json.loads(http_request.content)
        calls.append(payload)
        content = "{}" if len(calls) == 1 else json.dumps(valid_plan(question))
        return httpx.Response(200, json={"message": {"content": content}})

    monkeypatch.setattr(
        planner_module.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(responder)),
    )
    planner = OllamaResearchPlanner("fixture-model", "http://127.0.0.1:11434")
    result = await planner.plan(request)
    assert result.original_question == question
    assert len(calls) == 2
    assert calls[0]["format"]["type"] == "object"
    assert calls[0]["messages"][1]["content"].startswith("Original question:")
    assert calls[0]["stream"] is False
    assert calls[0]["options"]["temperature"] == 0
    assert all("snippet" not in json.dumps(call["messages"]).lower() for call in calls)


@pytest.mark.asyncio
async def test_ollama_rejects_second_invalid_plan(monkeypatch):
    import app.research.planner as planner_module

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        planner_module.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"message": {"content": "{}"}})
            )
        ),
    )
    with pytest.raises(ValueError, match="invalid plan twice"):
        await OllamaResearchPlanner("fixture", "http://127.0.0.1:11434").plan(
            ResearchRequest(question="Compare alpha and beta evidence")
        )


@pytest.mark.asyncio
async def test_ddgs_mapping_and_failure(monkeypatch):
    class FakeDDGS:
        def __init__(self, timeout):
            assert timeout == 10

        def text(self, query, max_results):
            assert query == "alpha evidence" and max_results == 2
            return [{"title": "Alpha", "href": "https://example.com/a", "body": "Evidence"}]

    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=FakeDDGS))
    provider = DDGSSearchProvider(retries=0)
    assert await provider.search("alpha evidence", 2) == [
        SearchResult("Alpha", "https://example.com/a", "Evidence", 1, "alpha evidence", "ddgs")
    ]

    def timeout(query, limit):
        raise TimeoutError("search timed out")

    monkeypatch.setattr(provider, "_search_sync", timeout)
    with pytest.raises(TimeoutError):
        await provider.search("alpha evidence", 2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
async def test_search_target_validation_rejects_private(url):
    with pytest.raises(UnsafeTarget):
        await TargetValidator().validate(url)


def test_research_input_and_url_validation():
    with pytest.raises(ValidationError):
        ResearchRequest(question=" ")
    with pytest.raises(ValidationError):
        ResearchRequest(question="Compare alpha and beta", max_subquestions=10000)
    with pytest.raises(ValidationError):
        ResearchRequest(question="Compare alpha and beta", max_total_pages=10000)
    with pytest.raises(InvalidURL):
        normalize_url("file:///etc/passwd")
    with pytest.raises(ValidationError):
        Settings(ollama_url="https://remote.example/api")
