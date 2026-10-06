import asyncio
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    rank: int
    query: str
    provider: str


class SearchProvider(Protocol):
    provider_name: str

    async def search(self, query: str, limit: int) -> list[SearchResult]: ...


class DDGSSearchProvider:
    provider_name = "ddgs"

    def __init__(self, retries: int = 2, timeout: int = 10) -> None:
        self.retries = retries
        self.timeout = timeout

    def _search_sync(self, query: str, limit: int) -> list[SearchResult]:
        from ddgs import DDGS

        rows = DDGS(timeout=self.timeout).text(query, max_results=limit)
        return [
            SearchResult(
                title=str(row.get("title") or "")[:500],
                url=str(row.get("href") or ""),
                snippet=str(row.get("body") or "")[:1500],
                rank=rank,
                query=query,
                provider=self.provider_name,
            )
            for rank, row in enumerate(rows, 1)
        ]

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        for attempt in range(self.retries + 1):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._search_sync, query, limit), timeout=self.timeout + 5
                )
            except Exception:
                if attempt == self.retries:
                    raise
                await asyncio.sleep(min(2**attempt, 4))
        raise AssertionError("unreachable")
