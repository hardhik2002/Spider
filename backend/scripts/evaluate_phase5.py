"""Run the controlled Phase 5 graph fixtures and save measured results.

Execute from backend: python scripts/evaluate_phase5.py
The benchmark uses a local HTTP fixture, fake embeddings and deterministic assessors.
It never labels the separate live smoke as a benchmark.
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.evaluation.phase5 import retrieval_metrics, run_metrics  # noqa: E402


def main() -> None:
    backend = BACKEND
    output = backend.parent / "docs" / "phase5-agent-results.json"
    with tempfile.TemporaryDirectory(prefix="spidermind-phase5-") as report_dir:
        env = os.environ.copy()
        env["SPIDERMIND_PHASE5_REPORT_DIR"] = report_dir
        command = [sys.executable, "-m", "pytest", "-q", "tests/test_phase5.py"]
        completed = subprocess.run(command, cwd=backend, env=env, check=False)
        if completed.returncode:
            raise SystemExit(completed.returncode)
        cases = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in Path(report_dir).glob("*.json")
        }
    required = {
        "enough",
        "budget",
        "stagnation",
        "partial",
        "acquisition",
        "resume",
        "failure",
        "prompt_injection",
        "failed_page_budget",
        "time_budget",
        "cancel",
    }
    if required - cases.keys():
        raise RuntimeError(f"Missing fixture outputs: {sorted(required - cases.keys())}")
    scenarios = {}
    for name in ("enough", "budget", "stagnation", "partial", "acquisition", "failed_page_budget"):
        case = cases[name]
        scenarios[name] = {
            "metrics": run_metrics(case["status"], case["gaps"], case["request"]),
            "iteration_trace": [
                {
                    "iteration": item["iteration"],
                    "nodes": [event["node"] for event in item["trace"]],
                    "events": item["trace"],
                }
                for item in case["iterations"]
            ],
        }
    acquired = cases["acquisition"]
    gold_chunks = set(acquired["relevant_chunk_ids"])
    gold_sources = set(acquired["relevant_source_urls"])
    one_pass = retrieval_metrics(acquired["baseline_results"], gold_chunks, gold_sources)
    agentic = retrieval_metrics(acquired["agentic_results"], gold_chunks, gold_sources)
    baseline_chunks = {row["chunk_id"] for row in acquired["baseline_results"]}
    final_chunks = {row["chunk_id"] for row in acquired["agentic_results"]}
    baseline_sources = {row["source_url"] for row in acquired["baseline_results"]}
    final_sources = {row["source_url"] for row in acquired["agentic_results"]}
    result = {
        "evaluation": "controlled Phase 5 fixture; actual graph, crawler, index and retrieval",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "fixture": {
            "question": "What are the benchmark metrics?",
            "subquestions": 1,
            "gold_relevant_source_urls": sorted(gold_sources),
            "gold_relevant_chunk_count": len(gold_chunks),
            "source_label_basis": "two fixture documents known relevant by construction",
            "search_provider": "deterministic local fixture",
            "embedding_model": "fixture two-dimensional embedding",
            "assessor": "deterministic source-count assessor",
            "reranker": "disabled",
            "k": 8,
        },
        "one_pass_vs_agentic": {
            "one_pass": {
                **one_pass,
                "subquestion_sufficiency_rate": 0.0,
                "search_queries": 0,
                "crawl_pages": 0,
            },
            "agentic": {
                **agentic,
                "subquestion_sufficiency_rate": scenarios["acquisition"]["metrics"][
                    "subquestion_sufficiency_rate"
                ],
                "search_queries": acquired["status"]["new_queries_generated"],
                "crawl_pages": acquired["status"]["new_pages_crawled"],
            },
            "source_expansion": {
                "sources_before": len(baseline_sources),
                "sources_after": len(final_sources),
                "chunks_before": len(baseline_chunks),
                "chunks_after": len(final_chunks),
                "new_chunk_ids": sorted(final_chunks - baseline_chunks),
            },
        },
        "scenarios": scenarios,
        "failure": cases["failure"],
        "time_budget": cases["time_budget"],
        "cancel": cases["cancel"],
        "resume": cases["resume"],
        "prompt_injection": cases["prompt_injection"],
        "limitations": [
            "Single controlled research question and two relevant fixture sources.",
            "The deterministic assessor tests orchestration, not real model judgment.",
            "MRR is based on labeled relevant chunks in a small fixture.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {output}")
    print(
        json.dumps(
            {
                "scenarios": {
                    name: value["metrics"]["stop_reason"] for name, value in scenarios.items()
                },
                "one_pass": one_pass,
                "agentic": agentic,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
