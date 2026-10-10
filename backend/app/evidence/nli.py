"""Lazy, batched local NLI with validated model label mapping."""

import asyncio
import logging
import time
from typing import Protocol

from app.evidence.schemas import EvidenceRelationResult, RelationLabel

logger = logging.getLogger("spidermind.evidence.nli")


def label_indexes(id2label: dict, label2id: dict | None = None) -> dict[RelationLabel, int]:
    labels = {str(value).lower(): int(key) for key, value in id2label.items()}
    if label2id:
        labels.update({str(key).lower(): int(value) for key, value in label2id.items()})
    required = {label.value.lower(): label for label in RelationLabel}
    missing = required.keys() - labels.keys()
    if missing:
        raise ValueError(f"NLI model missing labels: {sorted(missing)}")
    indexes = {label: labels[name] for name, label in required.items()}
    if len(set(indexes.values())) != 3:
        raise ValueError("NLI labels map to duplicate class IDs")
    return indexes


class EvidenceRelationClassifier(Protocol):
    model_name: str
    pairs_classified: int
    nli_batches: int
    nli_duration_ms: int

    async def classify_many(self, pairs: list[tuple[str, str]]) -> list[EvidenceRelationResult]: ...


class TransformersNLIClassifier:
    def __init__(
        self,
        model_name: str,
        batch_size: int = 2,
        device: str = "cpu",
        fallback_model: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.fallback_model = fallback_model
        self.batch_size = batch_size
        self.device = device
        self.pairs_classified = 0
        self.nli_batches = 0
        self.nli_duration_ms = 0
        self._model = None
        self._tokenizer = None
        self._indexes = None
        self._lock = asyncio.Lock()

    def _load_named(self, name: str) -> None:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        model = AutoModelForSequenceClassification.from_pretrained(name)
        tokenizer = AutoTokenizer.from_pretrained(name)
        indexes = label_indexes(model.config.id2label, model.config.label2id)
        model.to(self.device)
        model.eval()
        self._model, self._tokenizer, self._indexes = model, tokenizer, indexes
        self.model_name = name

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            self._load_named(self.model_name)
        except Exception as exc:
            if not self.fallback_model or self.fallback_model == self.model_name:
                raise
            logger.warning(
                "nli_model_fallback requested_model=%s fallback_model=%s reason=%s",
                self.model_name,
                self.fallback_model,
                type(exc).__name__,
            )
            self._load_named(self.fallback_model)

    def _classify(self, pairs: list[tuple[str, str]]) -> list[EvidenceRelationResult]:
        import torch

        self._load()
        results = []
        for start in range(0, len(pairs), self.batch_size):
            batch = pairs[start : start + self.batch_size]
            encoded = self._tokenizer(
                [premise[:3000] for premise, _ in batch],
                [hypothesis[:1000] for _, hypothesis in batch],
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            encoded = {key: value.to(self.device) for key, value in encoded.items()}
            with torch.no_grad():
                scores = torch.softmax(self._model(**encoded).logits, dim=-1).cpu().tolist()
            for row in scores:
                entailment = float(row[self._indexes[RelationLabel.ENTAILMENT]])
                neutral = float(row[self._indexes[RelationLabel.NEUTRAL]])
                contradiction = float(row[self._indexes[RelationLabel.CONTRADICTION]])
                relation = max(
                    (
                        (RelationLabel.ENTAILMENT, entailment),
                        (RelationLabel.NEUTRAL, neutral),
                        (RelationLabel.CONTRADICTION, contradiction),
                    ),
                    key=lambda item: item[1],
                )[0]
                results.append(
                    EvidenceRelationResult(
                        relation=relation,
                        entailment_score=entailment,
                        neutral_score=neutral,
                        contradiction_score=contradiction,
                        model_name=self.model_name,
                    )
                )
            self.nli_batches += 1
        self.pairs_classified += len(pairs)
        return results

    async def classify_many(self, pairs: list[tuple[str, str]]) -> list[EvidenceRelationResult]:
        if not pairs:
            return []
        async with self._lock:
            started = time.monotonic()
            result = await asyncio.to_thread(self._classify, pairs)
            self.nli_duration_ms += int((time.monotonic() - started) * 1000)
            return result

    async def classify(self, claim: str, evidence_text: str) -> EvidenceRelationResult:
        return (await self.classify_many([(evidence_text, claim)]))[0]
