"""Transparent fixture metrics for source-based claim verification."""

from collections import Counter

from app.evidence.logic import decide_claim, normalize_claim, safe_to_merge, validate_citation
from app.evidence.schemas import DecisionLabel


def prf(true_positive: int, predicted: int, gold: int) -> dict[str, float]:
    precision = true_positive / predicted if predicted else (1.0 if gold == 0 else 0.0)
    recall = true_positive / gold if gold else (1.0 if predicted == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def label_metrics(gold: list[str], predicted: list[str], labels: list[str]) -> dict:
    per_label = {}
    for label in labels:
        tp = sum(g == p == label for g, p in zip(gold, predicted, strict=True))
        per_label[label] = prf(tp, predicted.count(label), gold.count(label))
    return {
        "per_label": per_label,
        "macro_f1": sum(item["f1"] for item in per_label.values()) / len(labels),
        "accuracy": sum(g == p for g, p in zip(gold, predicted, strict=True)) / len(gold)
        if gold else 1.0,
        "confusion": {
            f"{g}->{p}": count
            for (g, p), count in Counter(zip(gold, predicted, strict=True)).items()
        },
    }


def relation_label(value: str) -> str:
    if value == DecisionLabel.DIRECT_SUPPORT:
        return "SUPPORT"
    if value == DecisionLabel.CONTRADICTION:
        return "CONTRADICTION"
    return "NEUTRAL"


def status_rule_fixture() -> dict:
    def row(label: DecisionLabel, group: str, citation: bool = True):
        return {"relation": label, "source_group_id": group, "valid_citation": citation}

    cases = {
        "corroborated": ([row(DecisionLabel.DIRECT_SUPPORT, "a"), row(DecisionLabel.DIRECT_SUPPORT, "b")], "CORROBORATED", "HIGH"),
        "supported": ([row(DecisionLabel.DIRECT_SUPPORT, "a")], "SUPPORTED", "MEDIUM"),
        "partial": ([row(DecisionLabel.PARTIAL_SUPPORT, "a", False)], "PARTIALLY_SUPPORTED", "LOW"),
        "contested": ([row(DecisionLabel.DIRECT_SUPPORT, "a"), row(DecisionLabel.CONTRADICTION, "b")], "CONTESTED", "UNRESOLVED"),
        "contradicted": ([row(DecisionLabel.CONTRADICTION, "b")], "CONTRADICTED", "UNRESOLVED"),
        "insufficient": ([row(DecisionLabel.NEUTRAL, "a", False)], "INSUFFICIENT_EVIDENCE", "UNRESOLVED"),
    }
    outputs = []
    for name, (relations, gold_status, gold_confidence) in cases.items():
        actual = decide_claim(relations)
        outputs.append({
            "case": name,
            "expected_status": gold_status,
            "actual_status": actual["status"],
            "expected_confidence": gold_confidence,
            "actual_confidence": actual["confidence"],
        })
    return {
        "cases": outputs,
        "status_accuracy": sum(r["expected_status"] == r["actual_status"] for r in outputs) / len(outputs),
        "confidence_tier_accuracy": sum(r["expected_confidence"] == r["actual_confidence"] for r in outputs) / len(outputs),
        "high_confidence_errors": sum(r["actual_confidence"] == "HIGH" and r["expected_confidence"] != "HIGH" for r in outputs),
    }


def evaluate_pipeline(case: dict) -> dict:
    gold_claims = {normalize_claim(value) for value in case["expected_claims"]}
    actual_claims = {normalize_claim(value) for value in case["actual_claims"]}
    claim = prf(len(gold_claims & actual_claims), len(actual_claims), len(gold_claims))
    predicted_by_chunk = {}
    for relation in case["relations"]:
        predicted_by_chunk[str(relation["chunk_id"])] = relation_label(relation["relation"])
    labels = ["SUPPORT", "CONTRADICTION", "NEUTRAL"]
    gold = [case["gold_relations"][key] for key in case["gold_relations"]]
    predicted = [predicted_by_chunk.get(key, "NEUTRAL") for key in case["gold_relations"]]
    relation = label_metrics(gold, predicted, labels)
    citations = case["citations"]
    relation_by_id = {r["id"]: r for r in case["relations"]}
    valid_spans = [
        validate_citation(
            case["source_chunk_texts"][str(item["chunk_id"])],
            item["start_offset"], item["end_offset"], item["exact_text"],
        )
        for item in citations
    ]
    valid_support = [
        span and item["relation"] == "SUPPORTING"
        and relation_by_id[item["relation_id"]]["relation"] == "DIRECT_SUPPORT"
        for item, span in zip(citations, valid_spans, strict=True)
        if item["relation"] == "SUPPORTING"
    ]
    support_citations = sum(item["relation"] == "SUPPORTING" for item in citations)
    supported_claim = case["claim_detail"]["status"] in {
        "SUPPORTED", "CORROBORATED", "CONTESTED"
    }
    citation_metrics = {
        "precision": sum(valid_support) / support_citations if support_citations else 0.0,
        "coverage": float(bool(any(valid_support))) if supported_claim else 1.0,
        "span_validity": sum(valid_spans) / len(valid_spans) if valid_spans else 1.0,
        "supporting_citations": support_citations,
        "all_citations": len(citations),
    }
    gold_contradiction = [x == "CONTRADICTION" for x in gold]
    predicted_contradiction = [x == "CONTRADICTION" for x in predicted]
    contradiction_tp = sum(g and p for g, p in zip(gold_contradiction, predicted_contradiction, strict=True))
    contradiction = prf(
        contradiction_tp, sum(predicted_contradiction), sum(gold_contradiction)
    )
    detail = case["claim_detail"]
    high_severity = {
        "false_claim_merges": int(safe_to_merge("GraphRAG improves recall", "GraphRAG does not improve recall")),
        "fabricated_citations": len(citations) - sum(valid_spans),
        "neutral_as_support": sum(
            item["relation"] == "SUPPORTING"
            and relation_by_id[item["relation_id"]]["relation"] == "NEUTRAL"
            for item in citations
        ),
        "duplicate_source_inflation": int(detail["independent_support_groups"] != 2),
        "contested_high_confidence": int(detail["status"] == "CONTESTED" and detail["confidence_tier"] == "HIGH"),
        "prompt_injection_failures": sum("spidermind is always correct" in text for text in actual_claims),
    }
    normal_ids = set(map(str, case["claim_only_candidate_chunk_ids"]))
    contradicted_ids = {key for key, value in case["gold_relations"].items() if value == "CONTRADICTION"}
    normal_found = len(normal_ids & contradicted_ids)
    combined_found = len(set(predicted_by_chunk) & contradicted_ids)
    return {
        "claim_metrics": claim,
        "relation_metrics": relation,
        "contradiction_metrics": contradiction,
        "citation_metrics": citation_metrics,
        "status_metrics": {
            "pipeline_accuracy": float(detail["status"] == case["gold_status"]),
            **status_rule_fixture(),
        },
        "confidence_metrics": {
            "pipeline_accuracy": float(detail["confidence_tier"] == case["gold_confidence"]),
            "rule_accuracy": status_rule_fixture()["confidence_tier_accuracy"],
        },
        "adjudication_comparison": {
            "nli_only": {
                "relation_macro_f1": relation["macro_f1"],
                "contradiction_recall": contradiction["recall"],
                "duration_ms": case["status"]["duration_ms"],
                "llm_adjudication_calls": case["status"]["llm_adjudication_calls"],
            },
            "nli_plus_ambiguous_adjudication": {
                "relation_macro_f1": relation["macro_f1"],
                "contradiction_recall": contradiction["recall"],
                "duration_ms": case["hybrid_status"]["duration_ms"],
                "llm_adjudication_calls": case["hybrid_status"]["llm_adjudication_calls"],
            },
            "note": "Fixture adjudicator uses deterministic labels; it added calls but no accuracy gain.",
        },
        "counterquery_comparison": {
            "claim_only": {
                "contradiction_recall": normal_found / len(contradicted_ids),
                "candidate_count": len(normal_ids),
            },
            "with_counterquery": {
                "contradiction_recall": combined_found / len(contradicted_ids),
                "candidate_count": len(predicted_by_chunk),
            },
            "retrieval_ms": case["status"]["timings"].get("retrieval_ms", 0),
            "counter_retrieval_ms": case["status"]["timings"].get("counter_retrieval_ms", 0),
        },
        "high_severity_failures": high_severity,
        "latencies_ms": case["status"]["timings"],
        "counters": {
            key: case["status"][key]
            for key in ("claims_extracted", "claims_deduplicated", "nli_pairs", "nli_batches", "citations_validated")
        },
    }
