"""Measured Phase 5 run metrics from labeled controlled evidence."""

from collections.abc import Sequence


def retrieval_metrics(
    results: Sequence[dict],
    relevant_chunk_ids: set[int],
    relevant_source_urls: set[str],
    k: int = 8,
) -> dict:
    top = list(results[:k])
    ranked_chunks = [row["chunk_id"] for row in top]
    ranked_sources = [row["source_url"] for row in top]
    relevant_chunks = set(ranked_chunks) & relevant_chunk_ids
    relevant_sources = set(ranked_sources) & relevant_source_urls
    first_relevant = next(
        (rank for rank, row in enumerate(top, 1) if row["chunk_id"] in relevant_chunk_ids), None
    )
    return {
        f"relevant_evidence_recall@{k}": len(relevant_chunks) / len(relevant_chunk_ids)
        if relevant_chunk_ids
        else 0.0,
        "mrr": 1 / first_relevant if first_relevant else 0.0,
        "unique_relevant_sources": len(relevant_sources),
        f"relevant_source_recall@{k}": len(relevant_sources) / len(relevant_source_urls)
        if relevant_source_urls
        else 0.0,
        "retrieved_chunks": len(top),
        "retrieved_sources": len(set(ranked_sources)),
    }


def run_metrics(status: dict, gaps: list[dict], request: dict) -> dict:
    created = len(gaps)
    resolved = sum(gap["status"] == "RESOLVED" for gap in gaps)
    limits = {
        "iterations": (status["iterations_used"], request["max_iterations"]),
        "queries": (status["new_queries_generated"], request["max_new_search_queries"]),
        "seeds": (status["new_seeds_selected"], request["max_new_seeds"]),
        "pages": (status["new_page_attempts"], request["max_new_pages"]),
    }
    action_keys = [
        (action["kind"], action["data"].get("query") or action["data"].get("url"))
        for gap in gaps
        for action in gap.get("actions", [])
        if action["kind"] in {"SEARCH", "CRAWL"}
    ]
    duplicate_actions = len(action_keys) - len(set(action_keys))
    runtime_adherence = status["duration_ms"] <= request["max_runtime_seconds"] * 1000
    return {
        "subquestion_sufficiency_rate": status["subquestions_sufficient"]
        / status["subquestions_total"]
        if status["subquestions_total"]
        else 0.0,
        "gaps_created": created,
        "gaps_resolved": resolved,
        "gaps_unresolved": created - resolved,
        "gap_closure_rate": resolved / created if created else 1.0,
        "queries": status["new_queries_generated"],
        "searches": status["new_searches_executed"],
        "seeds": status["new_seeds_selected"],
        "pages": status["new_pages_crawled"],
        "page_attempts": status["new_page_attempts"],
        "iterations": status["iterations_used"],
        "runtime_ms": status["duration_ms"],
        "chunks_indexed": status["new_chunks_indexed"],
        "llm_calls": status["llm_calls"],
        "llm_failures": status["llm_failures"],
        "duplicate_action_rate": duplicate_actions / len(action_keys) if action_keys else 0.0,
        "runtime_budget_adherence": runtime_adherence,
        "budget_adherence": (
            all(used <= maximum for used, maximum in limits.values()) and runtime_adherence
        ),
        "stop_reason": status["stop_reason"],
    }
