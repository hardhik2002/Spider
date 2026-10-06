"""Dense, BM25, hybrid and reranked evidence retrieval."""

import time
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.crawler.embedding import EmbeddingProvider
from app.db.models import ResearchSeed, ResearchSubquestion
from app.rag import lexical
from app.rag.fusion import fuse
from app.rag.indexing import chunk_representation
from app.rag.models import KnowledgeChunk, KnowledgeChunkSource, KnowledgeDocument
from app.rag.reranker import Reranker
from app.rag.vector import VectorIndex
from app.schemas.rag import RetrievalRequest


class RetrievalService:
    def __init__(
        self,
        sessions: async_sessionmaker,
        settings: Settings,
        embedder: EmbeddingProvider,
        vectors: VectorIndex,
        reranker: Reranker,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.embedder = embedder
        self.vectors = vectors
        self.reranker = reranker

    async def _allowed(
        self, session, job_id: str, request: RetrievalRequest
    ) -> dict[int, tuple[KnowledgeChunk, KnowledgeChunkSource, KnowledgeDocument]]:
        stmt = (
            select(KnowledgeChunk, KnowledgeChunkSource, KnowledgeDocument)
            .join(KnowledgeChunkSource, KnowledgeChunkSource.chunk_id == KnowledgeChunk.id)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunkSource.document_id)
            .where(
                KnowledgeChunk.research_job_id == job_id,
                KnowledgeDocument.research_job_id == job_id,
                KnowledgeDocument.index_status == "COMPLETED",
            )
            .order_by(KnowledgeChunk.id, KnowledgeChunkSource.id)
        )
        if request.document_id:
            stmt = stmt.where(KnowledgeDocument.id == request.document_id)
        if request.source_domain:
            stmt = stmt.where(KnowledgeDocument.source_domain == request.source_domain.lower())
        if request.crawl_job_id:
            stmt = stmt.where(KnowledgeDocument.crawl_job_id == request.crawl_job_id)
        if request.subquestion_id:
            subquery = (
                select(ResearchSeed.crawl_job_id)
                .join(ResearchSubquestion, ResearchSubquestion.id == ResearchSeed.subquestion_id)
                .where(
                    ResearchSeed.research_job_id == job_id,
                    ResearchSubquestion.plan_id == request.subquestion_id,
                    ResearchSeed.selected.is_(True),
                    ResearchSeed.crawl_job_id.is_not(None),
                )
            )
            stmt = stmt.where(KnowledgeDocument.crawl_job_id.in_(subquery))
        rows = (await session.execute(stmt)).all()
        return {chunk.id: (chunk, source, document) for chunk, source, document in rows}

    async def retrieve(self, job_id: str, request: RetrievalRequest) -> dict:
        started = time.monotonic()
        s = self.settings
        dense_k = request.dense_top_k or s.dense_top_k
        lexical_k = request.lexical_top_k or s.lexical_top_k
        fusion_k = request.fusion_top_k or s.fusion_top_k
        final_k = request.final_top_k or s.final_top_k
        rerank = request.rerank if request.rerank is not None else s.rerank_enabled
        neighbors = (
            request.include_neighbor_context
            if request.include_neighbor_context is not None
            else s.neighbor_expansion_enabled
        )
        timings: dict[str, int] = {}
        async with self.sessions() as session:
            allowed = await self._allowed(session, job_id, request)
            vector_ids = {item[0].vector_id for item in allowed.values()}
            dense: list[tuple[int, float]] = []
            lexical_hits: list[tuple[int, float]] = []
            if allowed and request.retrieval_mode in ("dense", "hybrid"):
                t = time.monotonic()
                query_vector = await self.embedder.embed(request.query)
                timings["query_embedding_ms"] = int((time.monotonic() - t) * 1000)
                t = time.monotonic()
                point_hits = await self.vectors.search(query_vector, job_id, dense_k, vector_ids)
                by_vector = {item[0].vector_id: chunk_id for chunk_id, item in allowed.items()}
                dense = [(by_vector[pid], score) for pid, score in point_hits if pid in by_vector]
                timings["dense_search_ms"] = int((time.monotonic() - t) * 1000)
            if allowed and request.retrieval_mode in ("lexical", "hybrid"):
                t = time.monotonic()
                lexical_hits = await lexical.search(
                    session, request.query, job_id, lexical_k, set(allowed)
                )
                timings["lexical_search_ms"] = int((time.monotonic() - t) * 1000)
            t = time.monotonic()
            candidates = fuse(
                dense,
                lexical_hits,
                k=s.rrf_k,
                dense_weight=s.rrf_dense_weight,
                lexical_weight=s.rrf_lexical_weight,
                limit=fusion_k,
            )
            timings["fusion_ms"] = int((time.monotonic() - t) * 1000)
            fused_count = len(candidates)
            if rerank and candidates:
                t = time.monotonic()
                candidates = candidates[: s.reranker_candidate_count]
                passages = [
                    chunk_representation(
                        allowed[c.chunk_id][2].title,
                        allowed[c.chunk_id][1].heading,
                        allowed[c.chunk_id][0].text,
                    )
                    for c in candidates
                ]
                scores = await self.reranker.score(request.query, passages)
                if len(scores) != len(candidates):
                    raise RuntimeError("Reranker returned the wrong number of scores")
                for candidate, score in zip(candidates, scores, strict=True):
                    candidate.reranker_score = score
                candidates.sort(key=lambda c: (-c.reranker_score, -c.rrf_score, c.chunk_id))
                timings["rerank_ms"] = int((time.monotonic() - t) * 1000)
            selected = []
            per_document: Counter = Counter()
            for item in candidates:
                chunk, source, document = allowed[item.chunk_id]
                if per_document[document.id] >= s.max_final_chunks_per_document:
                    continue
                per_document[document.id] += 1
                context = []
                if neighbors and s.neighbor_window:
                    start = max(0, source.chunk_index - s.neighbor_window)
                    end = source.chunk_index + s.neighbor_window
                    neighbor_rows = (
                        await session.execute(
                            select(KnowledgeChunkSource, KnowledgeChunk)
                            .join(
                                KnowledgeChunk, KnowledgeChunk.id == KnowledgeChunkSource.chunk_id
                            )
                            .where(
                                KnowledgeChunkSource.document_id == document.id,
                                KnowledgeChunkSource.chunk_index.between(start, end),
                                KnowledgeChunkSource.id != source.id,
                            )
                            .order_by(KnowledgeChunkSource.chunk_index)
                        )
                    ).all()
                    token_budget = s.max_neighbor_context_tokens - chunk.token_count
                    for neighbor_source, neighbor_chunk in neighbor_rows:
                        if neighbor_chunk.token_count <= token_budget:
                            context.append(
                                {
                                    "chunk_id": neighbor_chunk.id,
                                    "chunk_index": neighbor_source.chunk_index,
                                    "text": neighbor_chunk.text,
                                }
                            )
                            token_budget -= neighbor_chunk.token_count
                selected.append(
                    {
                        "rank": len(selected) + 1,
                        "chunk_id": chunk.id,
                        "document_id": document.id,
                        "text": chunk.text,
                        "heading": source.heading,
                        "source_title": document.title,
                        "source_url": document.source_url,
                        "source_domain": document.source_domain,
                        "chunk_index": source.chunk_index,
                        "dense_rank": item.dense_rank,
                        "dense_score": item.dense_score,
                        "lexical_rank": item.lexical_rank,
                        "bm25_score": item.bm25_score,
                        "rrf_score": item.rrf_score,
                        "reranker_raw_score": item.reranker_score,
                        "research_job_id": job_id,
                        "crawl_job_id": document.crawl_job_id,
                        "crawled_page_id": document.crawled_page_id,
                        "neighbor_context": context,
                    }
                )
                if len(selected) >= final_k:
                    break
        timings["total_ms"] = int((time.monotonic() - started) * 1000)
        return {
            "query": request.query,
            "research_job_id": job_id,
            "retrieval_mode": request.retrieval_mode,
            "dense_candidates": len(dense),
            "lexical_candidates": len(lexical_hits),
            "fused_candidates": fused_count,
            "reranked_candidates": len(candidates) if rerank else 0,
            "results": selected,
            "timings": timings,
        }
