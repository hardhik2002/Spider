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

from app.evaluation.phase6 import evaluate_pipeline  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="spidermind-phase6-") as report_dir:
        env = os.environ.copy()
        env["SPIDERMIND_PHASE6_REPORT_DIR"] = report_dir
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_evidence_rules.py", "tests/test_evidence_engine.py"],
            cwd=BACKEND,
            env=env,
            check=False,
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)
        case = json.loads((Path(report_dir) / "pipeline.json").read_text(encoding="utf-8"))
    metrics = evaluate_pipeline(case)
    output = {
        "evaluation": "Controlled deterministic local fixture; model accuracy is not estimated by fake components",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "fixture": {
            "question": "Does GraphRAG improve recall on Dataset X?",
            "gold_claims": case["expected_claims"],
            "gold_relations_by_chunk_id": case["gold_relations"],
            "gold_status": case["gold_status"],
            "gold_confidence": case["gold_confidence"],
            "features": [
                "support", "independent corroboration", "direct contradiction", "contested",
                "neutral distractor", "mirrored source", "semantic paraphrase",
                "prompt injection",
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
        **metrics,
        "nli_comparison": {
            "base": {"status": "not measured in deterministic fixture"},
            "large": {
                "status": "skipped",
                "reason": "16 GB laptop had under 1 GB available memory during evaluation",
            },
        },
        "limitations": [
            "One claim in the integrated pipeline fixture; six deterministic status-rule cases are measured separately.",
            "Fake classifier and adjudicator metrics establish wiring and accounting, not real NLI quality.",
            "Counterquery improvement is measured on a constructed retrieval fixture.",
        ],
    }
    target = BACKEND.parent / "docs" / "phase6-evidence-results.json"
    target.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {target}")
    print(json.dumps({key: output[key] for key in (
        "claim_metrics", "relation_metrics", "contradiction_metrics",
        "citation_metrics", "high_severity_failures"
    )}, indent=2))


if __name__ == "__main__":
    main()
