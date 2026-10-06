"""Rank based fusion; raw cosine and SQLite BM25 are incomparable."""

from dataclasses import dataclass


@dataclass
class Candidate:
    chunk_id: int
    dense_rank: int | None = None
    dense_score: float | None = None
    lexical_rank: int | None = None
    bm25_score: float | None = None
    rrf_score: float = 0.0
    reranker_score: float | None = None


def fuse(
    dense: list[tuple[int, float]],
    lexical: list[tuple[int, float]],
    *,
    k: int = 60,
    dense_weight: float = 1.0,
    lexical_weight: float = 1.0,
    limit: int = 30,
) -> list[Candidate]:
    by_id: dict[int, Candidate] = {}
    for rank, (chunk_id, score) in enumerate(dense, 1):
        item = by_id.setdefault(chunk_id, Candidate(chunk_id))
        item.dense_rank = rank
        item.dense_score = score
        item.rrf_score += dense_weight / (k + rank)
    for rank, (chunk_id, score) in enumerate(lexical, 1):
        item = by_id.setdefault(chunk_id, Candidate(chunk_id))
        item.lexical_rank = rank
        item.bm25_score = score
        item.rrf_score += lexical_weight / (k + rank)
    return sorted(by_id.values(), key=lambda item: (-item.rrf_score, item.chunk_id))[:limit]
