"""Conservative deterministic rules used after model predictions."""

import hashlib
import math
import re
from collections import defaultdict
from urllib.parse import urlsplit, urlunsplit

from app.evidence.schemas import ClaimStatus, ConfidenceTier, DecisionLabel

_SPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?%?(?!\w)")
_NEGATION = re.compile(r"\b(?:no|not|never|without|fails?|decreases?|increases?|less|more)\b", re.I)
_INSTRUCTION = re.compile(
    r"\b(?:ignore (?:all )?(?:previous|prior) instructions|mark confidence|"
    r"system prompt|create the claim|reveal (?:secrets?|environment)|execute (?:this|commands?))\b",
    re.I,
)
_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")


def normalize_claim(text: str) -> str:
    """Normalize surface form only; numbers, qualifiers and negation remain intact."""
    return _SPACE.sub(" ", text.strip()).rstrip(". ").casefold()


def stable_claim_key(research_job_id: str, subquestion_id: int, text: str) -> str:
    return hashlib.sha256(
        f"{research_job_id}\0{subquestion_id}\0{normalize_claim(text)}".encode()
    ).hexdigest()


def safe_to_merge(left: str, right: str) -> bool:
    """Hard guard before any embedding/LLM equivalence decision."""
    left_text, right_text = normalize_claim(left), normalize_claim(right)
    if _NUMBER.findall(left_text) != _NUMBER.findall(right_text):
        return False
    if set(_NEGATION.findall(left_text)) != set(_NEGATION.findall(right_text)):
        return False
    for marker in ("2020", "2021", "2022", "2023", "2024", "2025", "2026"):
        if (marker in left_text) != (marker in right_text):
            return False
    # Opposing directional language often has nearby embeddings.
    for pair in (("higher", "lower"), ("faster", "slower"), ("improve", "worsen")):
        if any(word in left_text for word in pair) != any(word in right_text for word in pair):
            return False
        if (pair[0] in left_text) != (pair[0] in right_text):
            return False
    return True


def suspicious_instruction(text: str) -> bool:
    return bool(_INSTRUCTION.search(text))


def cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    divisor = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return numerator / divisor if divisor else 0.0


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def source_groups(documents: list) -> dict[int, str]:
    """Union documents sharing canonical URL, source URL, or exact document hash."""
    parents = {doc.id: doc.id for doc in documents}

    def root(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    seen: dict[str, int] = {}
    for doc in documents:
        keys = [f"url:{canonical_url(doc.canonical_url or doc.source_url)}"]
        if doc.content_hash:
            keys.append(f"hash:{doc.content_hash}")
        for key in keys:
            if key in seen:
                parents[root(doc.id)] = root(seen[key])
            else:
                seen[key] = doc.id
    grouped: dict[int, list[int]] = defaultdict(list)
    for doc in documents:
        grouped[root(doc.id)].append(doc.id)
    answer = {}
    for ids in grouped.values():
        key = hashlib.sha256(",".join(map(str, sorted(ids))).encode()).hexdigest()
        for doc_id in ids:
            answer[doc_id] = key
    return answer


def source_type(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith(".gov"):
        return "GOVERNMENT"
    if host in {"github.com", "gitlab.com"}:
        return "REPOSITORY"
    if host in {"arxiv.org", "pubmed.ncbi.nlm.nih.gov"}:
        return "PRIMARY_RESEARCH"
    if host.startswith("docs.") or "/docs/" in url:
        return "OFFICIAL_DOCUMENTATION"
    return "UNKNOWN"


def sentence_spans(text: str) -> list[tuple[int, int, str]]:
    spans = []
    for match in _SENTENCE.finditer(text):
        raw = match.group()
        leading = len(raw) - len(raw.lstrip())
        sentence = raw.strip()
        if sentence:
            start = match.start() + leading
            spans.append((start, start + len(sentence), sentence))
    return spans


def validate_citation(text: str, start: int, end: int, exact_text: str) -> bool:
    return 0 <= start < end <= len(text) and text[start:end] == exact_text


def decide_claim(relations: list[dict]) -> dict:
    """Only valid cited relations count toward a strong status or confidence."""
    support = {
        r["source_group_id"]
        for r in relations
        if r["relation"] == DecisionLabel.DIRECT_SUPPORT and r["valid_citation"]
    }
    contradiction = {
        r["source_group_id"]
        for r in relations
        if r["relation"] == DecisionLabel.CONTRADICTION and r["valid_citation"]
    }
    partial = {
        r["source_group_id"] for r in relations if r["relation"] == DecisionLabel.PARTIAL_SUPPORT
    }
    if support and contradiction:
        status = ClaimStatus.CONTESTED
    elif contradiction:
        status = ClaimStatus.CONTRADICTED
    elif len(support) >= 2:
        status = ClaimStatus.CORROBORATED
    elif support:
        status = ClaimStatus.SUPPORTED
    elif partial:
        status = ClaimStatus.PARTIALLY_SUPPORTED
    else:
        status = ClaimStatus.INSUFFICIENT_EVIDENCE
    confidence = {
        ClaimStatus.CORROBORATED: ConfidenceTier.HIGH,
        ClaimStatus.SUPPORTED: ConfidenceTier.MEDIUM,
        ClaimStatus.PARTIALLY_SUPPORTED: ConfidenceTier.LOW,
    }.get(status, ConfidenceTier.UNRESOLVED)
    return {
        "status": status,
        "confidence": confidence,
        "support_groups": len(support),
        "contradiction_groups": len(contradiction),
        "partial_groups": len(partial),
        "needs_more_verification": status
        in {ClaimStatus.CONTESTED, ClaimStatus.INSUFFICIENT_EVIDENCE},
    }
