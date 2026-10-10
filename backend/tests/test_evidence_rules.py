"""Pure Phase 6 safety and decision rules; no models or network."""

import pytest
from app.evidence.logic import (
    decide_claim,
    normalize_claim,
    safe_to_merge,
    sentence_spans,
    source_groups,
    stable_claim_key,
    validate_citation,
)
from app.evidence.nli import label_indexes
from app.evidence.schemas import ClaimStatus, ConfidenceTier, DecisionLabel, RelationLabel


def test_claim_normalization_preserves_numbers_and_conditions():
    text = "  GraphRAG  improved recall by 18% on Dataset X in 2026. "
    assert normalize_claim(text) == "graphrag improved recall by 18% on dataset x in 2026"
    assert stable_claim_key("job", 1, text) == stable_claim_key(
        "job", 1, "GraphRAG improved recall by 18% on Dataset X in 2026"
    )


@pytest.mark.parametrize(
    "left,right",
    [
        ("Latency decreased by 18%", "Latency decreased by 20%"),
        ("GraphRAG improves recall", "GraphRAG does not improve recall"),
        ("GraphRAG reduces latency", "GraphRAG increases latency"),
        ("System cost $10 in 2023", "System cost $10 in 2026"),
    ],
)
def test_contradictory_or_qualified_claims_never_merge(left, right):
    assert not safe_to_merge(left, right)


def test_nli_label_mapping_uses_model_config():
    assert label_indexes({0: "CONTRADICTION", 1: "ENTAILMENT", 2: "NEUTRAL"}) == {
        RelationLabel.CONTRADICTION: 0,
        RelationLabel.ENTAILMENT: 1,
        RelationLabel.NEUTRAL: 2,
    }
    with pytest.raises(ValueError, match="missing labels"):
        label_indexes({0: "LABEL_0", 1: "LABEL_1", 2: "LABEL_2"})


def test_exact_offsets_and_invalid_span():
    text = "First sentence. GraphRAG improves recall on Dataset X."
    start, end, exact = sentence_spans(text)[1]
    assert validate_citation(text, start, end, exact)
    assert not validate_citation(text, start + 1, end, exact)
    assert not validate_citation(text, -1, end, exact)


def test_status_and_confidence_require_valid_independent_citations():
    support = {"relation": DecisionLabel.DIRECT_SUPPORT, "valid_citation": True}
    first = {**support, "source_group_id": "a"}
    mirror = {**support, "source_group_id": "a"}
    second = {**support, "source_group_id": "b"}
    assert decide_claim([first, mirror])["status"] == ClaimStatus.SUPPORTED
    assert decide_claim([first, second])["confidence"] == ConfidenceTier.HIGH
    contested = decide_claim(
        [
            first,
            {
                "relation": DecisionLabel.CONTRADICTION,
                "valid_citation": True,
                "source_group_id": "c",
            },
        ]
    )
    assert contested["status"] == ClaimStatus.CONTESTED
    assert contested["confidence"] == ConfidenceTier.UNRESOLVED
    assert contested["needs_more_verification"]
    assert decide_claim([{**first, "valid_citation": False}])["status"] == (
        ClaimStatus.INSUFFICIENT_EVIDENCE
    )
    assert (
        decide_claim(
            [{"relation": DecisionLabel.NEUTRAL, "valid_citation": True, "source_group_id": "x"}]
        )["status"]
        == ClaimStatus.INSUFFICIENT_EVIDENCE
    )


def test_source_group_mirrors_share_identity():
    from types import SimpleNamespace

    docs = [
        SimpleNamespace(
            id=1, canonical_url=None, source_url="https://a.test/p", content_hash="same"
        ),
        SimpleNamespace(
            id=2, canonical_url=None, source_url="https://b.test/p", content_hash="same"
        ),
        SimpleNamespace(
            id=3, canonical_url=None, source_url="https://c.test/p", content_hash="other"
        ),
    ]
    groups = source_groups(docs)
    assert groups[1] == groups[2]
    assert groups[1] != groups[3]
