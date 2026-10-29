"""Small labeled real-NLI pilot; records errors instead of treating scores as truth."""

import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.evaluation.phase6 import label_metrics  # noqa: E402
from app.evidence.nli import TransformersNLIClassifier  # noqa: E402

PAIRS = [
    (
        "GraphRAG improves recall on Dataset X.",
        "GraphRAG improves recall on Dataset X.",
        "ENTAILMENT",
    ),
    (
        "GraphRAG does not improve recall on Dataset X.",
        "GraphRAG improves recall on Dataset X.",
        "CONTRADICTION",
    ),
    ("BM25 is a lexical ranking algorithm.", "GraphRAG improves recall on Dataset X.", "NEUTRAL"),
    (
        "Latency increased by 18% on Dataset X.",
        "Latency decreased by 18% on Dataset X.",
        "CONTRADICTION",
    ),
    ("In 2023, system A cost $10.", "In 2026, system A cost $10.", "NEUTRAL"),
    ("GraphRAG improves recall on Dataset Y.", "GraphRAG improves recall on Dataset X.", "NEUTRAL"),
]


async def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"
    classifier = TransformersNLIClassifier(name, batch_size=2, device="cpu")
    started = time.monotonic()
    results = await classifier.classify_many([(premise, claim) for premise, claim, _ in PAIRS])
    duration = int((time.monotonic() - started) * 1000)
    gold = [label for _, _, label in PAIRS]
    predicted = [item.relation for item in results]
    metrics = label_metrics(gold, predicted, ["ENTAILMENT", "NEUTRAL", "CONTRADICTION"])
    report = {
        "model": classifier.model_name,
        "device": classifier.device,
        "batch_size": classifier.batch_size,
        "pairs": [
            {"premise": premise, "hypothesis": hypothesis, "gold": label, **result.model_dump()}
            for (premise, hypothesis, label), result in zip(PAIRS, results, strict=True)
        ],
        "metrics": metrics,
        "duration_ms": duration,
        "pairs_classified": classifier.pairs_classified,
        "nli_batches": classifier.nli_batches,
        "nli_duration_ms": classifier.nli_duration_ms,
        "scores_are_truth_probabilities": False,
    }
    target = BACKEND.parent / "docs" / "phase6-nli-base-results.json"
    target.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"model": name, "metrics": metrics, "duration_ms": duration}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
