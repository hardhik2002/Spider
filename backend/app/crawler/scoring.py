import math
import time
from dataclasses import dataclass

from app.crawler.embedding import EmbeddingProvider


class ScoringFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class LinkCandidate:
    normalized_url: str
    anchor_text: str
    source_page_title: str | None
    link_context: str
    depth: int


@dataclass(frozen=True)
class RelevanceScore:
    relevance_score: float
    priority_score: float
    depth_penalty: float


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Embedding dimensions must match and be nonempty")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("Embedding must not be a zero vector")
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def candidate_representation(candidate: LinkCandidate, max_context_chars: int) -> str:
    return (
        f"Target URL: {candidate.normalized_url[:500]}\n"
        f"Anchor: {candidate.anchor_text[:200]}\n"
        f"Source page: {(candidate.source_page_title or '')[:200]}\n"
        f"Context: {candidate.link_context[:max_context_chars]}"
    )


def page_representation(
    title: str | None, description: str | None, content: str, max_content_chars: int
) -> str:
    return (
        f"Title: {(title or '')[:200]}\n"
        f"Description: {(description or '')[:300]}\n"
        f"Content: {content[:max_content_chars]}"
    )


class SemanticScorer:
    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        depth_penalty: float,
        max_context_chars: int,
        max_page_chars: int,
    ) -> None:
        self.provider = provider
        self.depth_penalty = depth_penalty
        self.max_context_chars = max_context_chars
        self.max_page_chars = max_page_chars
        self.query_vector: list[float] | None = None
        self.query_embedding_ms: int | None = None
        self.candidate_count = 0
        self.candidate_scoring_ms = 0
        self._candidate_cache: dict[str, list[float]] = {}

    async def prepare(self, query: str) -> None:
        if self.query_vector is not None:
            return
        started = time.monotonic()
        self.query_vector = await self.provider.embed(query)
        self.query_embedding_ms = int((time.monotonic() - started) * 1000)

    async def score_batch(self, candidates: list[LinkCandidate]) -> list[RelevanceScore]:
        if self.query_vector is None:
            raise RuntimeError("Research query must be embedded before scoring")
        started = time.monotonic()
        representations = [
            candidate_representation(candidate, self.max_context_chars) for candidate in candidates
        ]
        missing = list(
            dict.fromkeys(text for text in representations if text not in self._candidate_cache)
        )
        if missing:
            vectors = await self.provider.embed_many(missing)
            if len(vectors) != len(missing):
                raise ValueError("Embedding provider returned the wrong number of vectors")
            self._candidate_cache.update(zip(missing, vectors, strict=True))
            self.candidate_count += len(missing)
        scores = []
        for candidate, representation in zip(candidates, representations, strict=True):
            relevance = cosine_similarity(self.query_vector, self._candidate_cache[representation])
            penalty = self.depth_penalty * candidate.depth
            scores.append(RelevanceScore(relevance, relevance - penalty, penalty))
        self.candidate_scoring_ms += int((time.monotonic() - started) * 1000)
        return scores

    async def score_page(self, title: str | None, description: str | None, content: str) -> float:
        if self.query_vector is None:
            raise RuntimeError("Research query must be embedded before scoring")
        representation = page_representation(title, description, content, self.max_page_chars)
        return cosine_similarity(self.query_vector, await self.provider.embed(representation))
