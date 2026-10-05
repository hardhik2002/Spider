import asyncio
import logging
import time
from typing import Any, Protocol

logger = logging.getLogger("spidermind.embedding")


class EmbeddingProvider(Protocol):
    model_name: str
    load_time_ms: int | None

    async def embed(self, text: str) -> list[float]: ...

    async def embed_many(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerProvider:
    """One lazily loaded local model shared across crawl jobs."""

    def __init__(self, model_name: str = "BAAI/bge-m3", batch_size: int = 16) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.load_time_ms: int | None = None
        self._model: Any = None
        self._lock = asyncio.Lock()

    def _load_model(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        started = time.monotonic()
        self._model = SentenceTransformer(self.model_name)
        self.load_time_ms = int((time.monotonic() - started) * 1000)
        logger.info("embedding model loaded in %s ms", self.load_time_ms)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        self._load_model()
        vectors = self._model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [vector.astype(float).tolist() for vector in vectors]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with self._lock:
            return await asyncio.to_thread(self._encode, texts)

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]
