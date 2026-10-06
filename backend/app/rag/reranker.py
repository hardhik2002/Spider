"""Local cross-encoder pair scoring; raw logits are not probabilities."""

import asyncio
from typing import Protocol


class Reranker(Protocol):
    async def score(self, query: str, passages: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(self, model_name: str, device: str = "cpu", batch_size: int = 4) -> None:
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self._model = None
        self._lock = asyncio.Lock()

    def _score(self, query: str, passages: list[str]) -> list[float]:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name, device=self.device)
        import torch

        with torch.inference_mode():
            scores = self._model.predict(
                [(query, passage) for passage in passages],
                batch_size=self.batch_size,
                activation_fn=torch.nn.Identity(),
                show_progress_bar=False,
            )
        return [float(score) for score in scores]

    async def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        async with self._lock:
            return await asyncio.to_thread(self._score, query, passages)
