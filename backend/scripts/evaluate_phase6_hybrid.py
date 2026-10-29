"""Tiny real Qwen adjudication pilot on saved labeled NLI pairs."""

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.core.config import Settings  # noqa: E402
from app.evaluation.phase6 import label_metrics  # noqa: E402
from app.evidence.llm import OllamaEvidenceLLM  # noqa: E402
from app.evidence.logic import apply_qualifier_safety, scope_mismatch  # noqa: E402
from app.evidence.schemas import (  # noqa: E402
    ClaimType,
    DecisionLabel,
    EvidenceRelationResult,
    EvidenceRequest,
)
from app.evidence.service import EvidenceEngineService  # noqa: E402


def relation_from_decision(decision: DecisionLabel) -> str:
    return {
        DecisionLabel.DIRECT_SUPPORT: "ENTAILMENT",
        DecisionLabel.CONTRADICTION: "CONTRADICTION",
    }.get(decision, "NEUTRAL")


async def main() -> None:
    pilot = json.loads((ROOT / "docs" / "phase6-nli-base-results.json").read_text("utf-8"))
    settings = Settings()
    verifier = EvidenceEngineService.__new__(EvidenceEngineService)
    verifier.settings = settings
    request = EvidenceRequest(use_llm_adjudication=True)
    llm = OllamaEvidenceLLM(
        settings.agent_model or settings.ollama_model,
        settings.ollama_url,
        300,
        0,
    )
    labels = ["ENTAILMENT", "NEUTRAL", "CONTRADICTION"]
    gold = [row["gold"] for row in pilot["pairs"]]
    baseline = []
    hybrid = []
    calls = []
    started = time.monotonic()
    for index, row in enumerate(pilot["pairs"]):
        nli = EvidenceRelationResult.model_validate(
            {key: row[key] for key in EvidenceRelationResult.model_fields}
        )
        claim_type = (
            ClaimType.NUMERIC
            if index == 3
            else ClaimType.TEMPORAL
            if index == 4
            else ClaimType.DESCRIPTIVE
        )
        mismatch = scope_mismatch(row["hypothesis"], row["premise"])
        base_relation = "NEUTRAL" if mismatch else row["relation"]
        baseline.append(base_relation)
        final_relation = base_relation
        if verifier._needs_adjudication(
            SimpleNamespace(claim_type=claim_type), nli, False, request
        ):
            call_started = time.monotonic()
            try:
                decision = await llm.adjudicate(row["hypothesis"], row["premise"], nli)
                guarded = apply_qualifier_safety(
                    decision.relation, decision.important_qualifier_mismatch or mismatch
                )
                final_relation = relation_from_decision(guarded)
                calls.append(
                    {
                        "pair_index": index,
                        "decision": decision.model_dump(mode="json"),
                        "final_relation": final_relation,
                        "latency_ms": int((time.monotonic() - call_started) * 1000),
                    }
                )
            except Exception as exc:
                calls.append(
                    {
                        "pair_index": index,
                        "error": f"{type(exc).__name__}: {exc}"[:300],
                        "latency_ms": int((time.monotonic() - call_started) * 1000),
                    }
                )
        hybrid.append(final_relation)
    base_metrics = label_metrics(gold, baseline, labels)
    hybrid_metrics = label_metrics(gold, hybrid, labels)
    report = {
        "kind": "tiny six-pair labeled real-model pilot, not a representative benchmark",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "nli_model": pilot["model"],
        "adjudicator_model": llm.model,
        "fixture_size": len(gold),
        "baseline_scope_guarded_metrics": base_metrics,
        "hybrid_metrics": hybrid_metrics,
        "llm_call_count": len(calls),
        "llm_calls": calls,
        "baseline_predictions": baseline,
        "hybrid_predictions": hybrid,
        "gold": gold,
        "total_adjudication_ms": int((time.monotonic() - started) * 1000),
        "nli_latency_ms_from_prior_pilot": pilot["duration_ms"],
        "limitations": [
            "Only two of six pairs meet the configured ambiguity/type trigger.",
            "The NLI baseline is reused from a previous run; total times are not "
            "a paired end-to-end benchmark.",
        ],
    }
    target = ROOT / "docs" / "phase6-hybrid-pilot-results.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {target}")
    print(
        json.dumps(
            {
                "baseline_macro_f1": base_metrics["macro_f1"],
                "hybrid_macro_f1": hybrid_metrics["macro_f1"],
                "llm_call_count": len(calls),
                "total_adjudication_ms": report["total_adjudication_ms"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
