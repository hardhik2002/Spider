"""Persistent local Qdrant adapter behind a small vector-index interface."""

import asyncio
from pathlib import Path
from typing import Protocol

from qdrant_client import QdrantClient, models


class VectorIndex(Protocol):
    async def upsert(self, points: list[tuple[str, list[float], dict]]) -> None: ...
    async def search(
        self,
        vector: list[float],
        research_job_id: str,
        limit: int,
        allowed_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]: ...
    async def delete(self, ids: list[str]) -> None: ...
    async def existing(self, ids: list[str]) -> set[str]: ...
    async def count(self, research_job_id: str) -> int: ...

    async def ids(self, research_job_id: str) -> set[str]: ...
    async def close(self) -> None: ...


class QdrantVectorIndex:
    def __init__(self, path: str, collection: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)
        self.client = QdrantClient(path=path, force_disable_check_same_thread=True)
        self.collection = collection
        self._lock = asyncio.Lock()

    def _ensure_collection(self, dimension: int) -> None:
        if self.client.collection_exists(self.collection):
            actual = self.client.get_collection(self.collection).config.params.vectors.size
            if actual != dimension:
                raise ValueError(
                    f"Qdrant dimension {actual} differs from embedding dimension {dimension}"
                )
        else:
            self.client.create_collection(
                self.collection,
                vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
            )

    async def upsert(self, points: list[tuple[str, list[float], dict]]) -> None:
        if not points:
            return
        async with self._lock:
            await asyncio.to_thread(self._upsert, points)

    def _upsert(self, points):
        self._ensure_collection(len(points[0][1]))
        for start in range(0, len(points), 64):
            batch = points[start : start + 64]
            self.client.upsert(
                self.collection,
                [
                    models.PointStruct(id=point_id, vector=vector, payload=payload)
                    for point_id, vector, payload in batch
                ],
                wait=True,
            )

    async def search(
        self,
        vector: list[float],
        research_job_id: str,
        limit: int,
        allowed_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        if allowed_ids == set():
            return []
        async with self._lock:
            return await asyncio.to_thread(
                self._search, vector, research_job_id, limit, allowed_ids
            )

    def _search(self, vector, research_job_id, limit, allowed_ids):
        if not self.client.collection_exists(self.collection):
            return []
        must = [
            models.FieldCondition(
                key="research_job_id", match=models.MatchValue(value=research_job_id)
            )
        ]
        if allowed_ids is not None:
            must.append(models.HasIdCondition(has_id=list(allowed_ids)))
        result = self.client.query_points(
            self.collection,
            query=vector,
            query_filter=models.Filter(must=must),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [(str(point.id), float(point.score)) for point in result.points]

    async def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        async with self._lock:
            await asyncio.to_thread(self._delete, ids)

    def _delete(self, ids):
        if self.client.collection_exists(self.collection):
            self.client.delete(self.collection, points_selector=ids, wait=True)

    async def existing(self, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        async with self._lock:
            return await asyncio.to_thread(self._existing, ids)

    def _existing(self, ids):
        if not self.client.collection_exists(self.collection):
            return set()
        found = set()
        for start in range(0, len(ids), 256):
            found.update(
                str(point.id)
                for point in self.client.retrieve(
                    self.collection, ids[start : start + 256], with_payload=False
                )
            )
        return found

    async def count(self, research_job_id: str) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._count, research_job_id)

    def _count(self, research_job_id):
        if not self.client.collection_exists(self.collection):
            return 0
        condition = models.Filter(
            must=[
                models.FieldCondition(
                    key="research_job_id", match=models.MatchValue(value=research_job_id)
                )
            ]
        )
        return self.client.count(self.collection, count_filter=condition, exact=True).count

    async def ids(self, research_job_id: str) -> set[str]:
        async with self._lock:
            return await asyncio.to_thread(self._ids, research_job_id)

    def _ids(self, research_job_id):
        if not self.client.collection_exists(self.collection):
            return set()
        condition = models.Filter(
            must=[
                models.FieldCondition(
                    key="research_job_id", match=models.MatchValue(value=research_job_id)
                )
            ]
        )
        result = set()
        offset = None
        while True:
            points, offset = self.client.scroll(
                self.collection,
                scroll_filter=condition,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            result.update(str(point.id) for point in points)
            if offset is None:
                return result

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self.client.close)
