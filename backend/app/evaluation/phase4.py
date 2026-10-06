"""Deterministic retrieval metrics. Labels are document IDs, never model scores."""

import math
from collections.abc import Mapping, Sequence


def ranking_metrics(
    ranked: Sequence[str | int], relevant: Mapping[str | int, float], k: int = 5
) -> dict[str, float]:
    top = list(ranked[:k])
    hits = [item for item in top if relevant.get(item, 0) > 0]
    ideal = sorted((float(v) for v in relevant.values() if v > 0), reverse=True)[:k]
    dcg = sum(
        (2 ** relevant.get(item, 0) - 1) / math.log2(rank + 1) for rank, item in enumerate(top, 1)
    )
    idcg = sum((2**value - 1) / math.log2(rank + 1) for rank, value in enumerate(ideal, 1))
    first = next((rank for rank, item in enumerate(ranked, 1) if relevant.get(item, 0) > 0), None)
    return {
        f"recall@{k}": len(set(hits)) / sum(v > 0 for v in relevant.values()) if ideal else 0.0,
        f"precision@{k}": len(hits) / k,
        "mrr": 1 / first if first else 0.0,
        f"ndcg@{k}": dcg / idcg if idcg else 0.0,
        f"hit_rate@{k}": float(bool(hits)),
    }


def aggregate(rows: Sequence[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {}
    return {key: sum(row[key] for row in rows) / len(rows) for key in rows[0]}
