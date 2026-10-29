"""Run controlled Phase 6 pipeline fixtures and write measured JSON metrics."""

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.evaluation.phase6 import evaluate_pipeline, label_metrics, prf  # noqa: E402
from app.evidence.logic import safe_to_merge, scope_mismatch  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="spidermind-phase6-") as report_dir:
        env = os.environ.copy()
        env["SPIDERMIND_PHASE6_REPORT_DIR"] = report_dir
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_evidence_rules.py",
                "tests/test_evidence_engine.py",
                "tests/test_evidence_llm.py",
            ],
            cwd=BACKEND,
            env=env,
            check=False,
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)
        case = json.loads((Path(report_dir) / "pipeline.json").read_text(encoding="utf-8"))
    metrics = evaluate_pipeline(case)
    qualifier_cases = [
        {
            "feature": "numeric variation",
            "left": "Latency decreased by 18% on Dataset X.",
            "right": "Latency decreased by 20% on Dataset X.",
            "expected_merge": False,
        },
        {
            "feature": "temporal variation",
            "left": "System A cost $10 in 2023.",
            "right": "System A cost $10 in 2026.",
            "expected_merge": False,
        },
        {
            "feature": "condition-dependent variation",
            "left": "System A is accurate when labels exist.",
            "right": "System A is accurate.",
            "expected_merge": False,
        },
        {
            "feature": "dataset variation",
            "left": "GraphRAG improves recall on Dataset X.",
            "right": "GraphRAG improves recall on Dataset Y.",
            "expected_merge": False,
        },
        {
            "feature": "negated contradiction",
            "left": "GraphRAG improves recall on Dataset X.",
            "right": "GraphRAG does not improve recall on Dataset X.",
            "expected_merge": False,
        },
    ]
    for row in qualifier_cases:
        row["actual_merge"] = safe_to_merge(row["left"], row["right"])
        if row["actual_merge"] != row["expected_merge"]:
            raise AssertionError(f"Qualifier safety failed: {row['feature']}")
    dedup_gold = [True, *(False for _ in qualifier_cases)]
    dedup_predicted = [
        case["status"]["claims_deduplicated"] == 1,
        *(row["actual_merge"] for row in qualifier_cases),
    ]
    dedup_tp = sum(
        expected and actual for expected, actual in zip(dedup_gold, dedup_predicted, strict=True)
    )
    dedup_metrics = {
        **prf(dedup_tp, sum(dedup_predicted), sum(dedup_gold)),
        "gold_merge_pairs": sum(dedup_gold),
        "gold_nonmerge_pairs": len(dedup_gold) - sum(dedup_gold),
    }
    gold_quotes = {
        chunk_id: (
            "GraphRAG improves recall on Dataset X."
            if label == "SUPPORT" and "GraphRAG improves recall on Dataset X." in text
            else "On Dataset X, GraphRAG improves recall compared with RAG."
            if label == "SUPPORT"
            else "GraphRAG does not improve recall on Dataset X."
        )
        for chunk_id, label in case["gold_relations"].items()
        if label != "NEUTRAL"
        for text in [case["source_chunk_texts"][chunk_id]]
    }
    gold_spans = [
        {
            "chunk_id": int(chunk_id),
            "start_offset": case["source_chunk_texts"][chunk_id].index(quote),
            "end_offset": case["source_chunk_texts"][chunk_id].index(quote) + len(quote),
            "exact_text": quote,
        }
        for chunk_id, quote in gold_quotes.items()
    ]
    pilot_path = BACKEND.parent / "docs" / "phase6-nli-base-results.json"
    if pilot_path.exists():
        pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
        guarded = [
            "NEUTRAL"
            if scope_mismatch(row["hypothesis"], row["premise"])
            and row["relation"] in {"ENTAILMENT", "CONTRADICTION"}
            else row["relation"]
            for row in pilot["pairs"]
        ]
        guarded_metrics = label_metrics(
            [row["gold"] for row in pilot["pairs"]],
            guarded,
            ["ENTAILMENT", "NEUTRAL", "CONTRADICTION"],
        )
        base_comparison = {
            "status": "measured",
            "model": pilot["model"],
            "device": pilot["device"],
            "batch_size": pilot["batch_size"],
            "raw_metrics": pilot["metrics"],
            "scope_guarded_metrics": guarded_metrics,
            "latency_ms": pilot["duration_ms"],
            "batches": pilot["nli_batches"],
            "fixture_size": len(pilot["pairs"]),
            "pairs": pilot["pairs"],
            "scores_are_truth_probabilities": False,
        }
    else:
        base_comparison = {"status": "not measured"}
    hybrid_path = BACKEND.parent / "docs" / "phase6-hybrid-pilot-results.json"
    hybrid_pilot = (
        json.loads(hybrid_path.read_text(encoding="utf-8"))
        if hybrid_path.exists()
        else {"status": "not measured"}
    )
    output = {
        "evaluation": (
            "Controlled deterministic local fixture; model accuracy "
            "is not estimated by fake components"
        ),
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "fixture": {
            "question": "Does GraphRAG improve recall on Dataset X?",
            "gold_claims": case["expected_claims"],
            "gold_relations_by_chunk_id": case["gold_relations"],
            "gold_equivalence_group": [
                "GraphRAG improves recall on Dataset X.",
                "On Dataset X, GraphRAG improves recall.",
            ],
            "gold_valid_citation_spans": gold_spans,
            "gold_status": case["gold_status"],
            "gold_confidence": case["gold_confidence"],
            "additional_gold_cases": qualifier_cases,
            "features": [
                "support",
                "independent corroboration",
                "direct contradiction",
                "contested",
                "neutral distractor",
                "mirrored source",
                "semantic paraphrase",
                "prompt injection",
                "partial support status rule",
                "numeric variation",
                "temporal variation",
                "condition-dependent variation",
            ],
        },
        "models": {
            "fixture_extractor": "FakeExtractor",
            "fixture_relation_classifier": "FakeClassifier",
            "fixture_adjudicator": "FakeAdjudicator",
            "configured_nli_default": "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli",
        },
        "configuration": {
            "adjudication_modes": ["NLI only", "ambiguous-case adjudication"],
            "retrieval_modes": ["claim query", "claim plus counterquery"],
            "no_internet": True,
        },
        "qualifier_guard_accuracy": 1.0,
        "deduplication_metrics": dedup_metrics,
        **metrics,
        "nli_comparison": {
            "base": base_comparison,
            "large": {
                "status": "skipped",
                "reason": "16 GB laptop had under 1 GB available memory during evaluation",
            },
        },
        "real_adjudication_pilot": hybrid_pilot,
        "limitations": [
            "One claim in the integrated pipeline fixture; six deterministic "
            "status-rule cases are measured separately.",
            "Fake classifier and adjudicator metrics establish wiring and accounting, "
            "not real NLI quality.",
            "Counterquery improvement is measured on a constructed retrieval fixture.",
        ],
    }
    target = BACKEND.parent / "docs" / "phase6-evidence-results.json"
    target.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {target}")
    print(
        json.dumps(
            {
                key: output[key]
                for key in (
                    "claim_metrics",
                    "relation_metrics",
                    "contradiction_metrics",
                    "citation_metrics",
                    "high_severity_failures",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
