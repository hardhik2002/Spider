import asyncio
from pathlib import Path

import httpx
import pytest
from app.core.config import Settings
from app.db.models import (
    CrawledPage,
    CrawlJob,
    ResearchJob,
    ResearchSearchResult,
    ResearchSeed,
    ResearchSubquestion,
)
from app.evaluation.phase4 import ranking_metrics
from app.main import create_app
from app.rag.chunking import chunk_text
from app.rag.fusion import fuse
from app.rag.lexical import fallback_bm25, safe_fts_query
from app.rag.models import KnowledgeChunk, KnowledgeChunkSource, KnowledgeDocument
from sqlalchemy import func, select, text


class WordTokenizer:
    def __init__(self):
        self.ids = {}
        self.words = {}

    def encode(self, value):
        out = []
        for word in value.split():
            if word not in self.ids:
                key = len(self.ids) + 1
                self.ids[word] = key
                self.words[key] = word
            out.append(self.ids[word])
        return out

    def decode(self, tokens):
        return " ".join(self.words[token] for token in tokens)


class CharacterTokenizer:
    def encode(self, value):
        return [ord(char) for char in value]

    def decode(self, tokens):
        return "".join(chr(token) for token in tokens)


class FakeEmbedding:
    model_name = "fixture-vector"
    load_time_ms = 0

    async def embed(self, value):
        return (await self.embed_many([value]))[0]

    async def embed_many(self, values):
        terms = ("graphrag", "bm25", "latency", "hallucination")
        return [[float(value.lower().count(term)) + 0.01 for term in terms] for value in values]


class FakeReranker:
    async def score(self, query, passages):
        terms = set(query.lower().split())
        return [float(len(terms & set(p.lower().split()))) for p in passages]


def test_chunking_and_fusion():
    tokenizer = WordTokenizer()
    passages = chunk_text(
        "# Heading\n\n" + " ".join(f"word{i}" for i in range(55)),
        tokenizer,
        target_tokens=12,
        max_tokens=16,
        overlap_tokens=2,
        min_tokens=3,
    )
    assert len(passages) > 2
    assert all(0 < p.token_count <= 16 for p in passages)
    assert all(p.heading == "Heading" for p in passages)
    source = "GraphRAG connects entities. GraphRAG retrieves evidence. " * 5
    character_passages = chunk_text(
        source,
        CharacterTokenizer(),
        target_tokens=50,
        max_tokens=70,
        overlap_tokens=8,
        min_tokens=5,
    )
    assert all(word in source.split() for p in character_passages for word in p.text.split())
    assert all(p.token_count <= 70 for p in character_passages)
    ranked = fuse([(1, 0.8), (2, 0.7)], [(2, -3), (3, -2)])
    assert ranked[0].chunk_id == 2
    assert ranked[0].dense_rank == 2 and ranked[0].lexical_rank == 1
    assert '"GPT-5"' in safe_fts_query("GPT-5: (GraphRAG)")
    metrics = ranking_metrics(["x", "a", "b"], {"a": 2, "b": 1}, k=2)
    assert metrics["recall@2"] == 0.5
    assert metrics["precision@2"] == 0.5
    assert metrics["mrr"] == 0.5


@pytest.mark.asyncio
async def test_index_retrieve_idempotency_and_replacement(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'rag.db'}",
        qdrant_path=str(tmp_path / "qdrant"),
        chunk_target_tokens=50,
        chunk_max_tokens=80,
        chunk_overlap_tokens=5,
        chunk_min_tokens=5,
        rerank_enabled=False,
        neighbor_expansion_enabled=False,
    )
    app = create_app(
        settings,
        embedding_provider=FakeEmbedding(),
        reranker=FakeReranker(),
        tokenizer=WordTokenizer(),
    )
    async with app.router.lifespan_context(app):
        async with app.state.index_service.sessions() as session:
            research = ResearchJob(
                question="GraphRAG and BM25",
                request_json="{}",
                planner_model="fixture",
                max_subquestions=1,
                max_total_pages=3,
            )
            crawl = CrawlJob(
                start_url="https://example.org/a",
                max_depth=0,
                max_pages=2,
                timeout=10,
                max_content_size=100000,
            )
            session.add_all([research, crawl])
            await session.flush()
            page = CrawledPage(
                crawl_job_id=crawl.id,
                url="https://example.org/a",
                normalized_url="https://example.org/a",
                depth=0,
                title="GraphRAG overview",
                crawl_status="COMPLETED",
                text_content="GraphRAG uses a graph of entities for retrieval. " * 8,
            )
            sub = ResearchSubquestion(
                research_job_id=research.id,
                plan_id="architecture",
                question="How does GraphRAG work?",
                rationale="x",
                priority="high",
                expected_evidence="[]",
                preferred_source_types="[]",
                order=0,
            )
            result = ResearchSearchResult(
                research_job_id=research.id,
                normalized_url="https://example.org/a",
                url="https://example.org/a",
                title="GraphRAG",
                snippet="graph",
                domain="example.org",
                provider="fixture",
                best_rank=1,
            )
            session.add_all([page, sub, result])
            await session.flush()
            session.add(
                ResearchSeed(
                    research_job_id=research.id,
                    subquestion_id=sub.id,
                    result_id=result.id,
                    semantic_relevance=0.9,
                    seed_score=0.9,
                    selected=True,
                    crawl_job_id=crawl.id,
                )
            )
            await session.commit()
            job_id = research.id
            page_id = page.id
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/api/v1/research/{job_id}/index")
            assert response.status_code == 202
            await asyncio.gather(*app.state.index_service.tasks)
            status = (await client.get(f"/api/v1/research/{job_id}/index")).json()
            assert status["status"] == "COMPLETED", status
            assert status["vector_records"] == status["lexical_records"] > 0
            consistency = (await client.get(f"/api/v1/research/{job_id}/index/consistency")).json()
            assert consistency["consistent"], consistency
            result = (
                await client.post(
                    f"/api/v1/research/{job_id}/retrieve",
                    json={
                        "query": "GraphRAG entities",
                        "rerank": True,
                        "subquestion_id": "architecture",
                    },
                )
            ).json()
            assert result["results"], result
            assert result["results"][0]["reranker_raw_score"] is not None
            assert result["results"][0]["source_url"] == "https://example.org/a"
            assert (
                await client.post(
                    f"/api/v1/research/{job_id}/retrieve",
                    json={"query": "GraphRAG", "source_domain": "other.example", "rerank": False},
                )
            ).json()["results"] == []
            assert (
                await client.post(
                    f"/api/v1/research/{job_id}/retrieve",
                    json={"query": "GraphRAG", "subquestion_id": "other", "rerank": False},
                )
            ).json()["results"] == []
            async with app.state.index_service.sessions() as session:
                fallback = await fallback_bm25(session, "GraphRAG", job_id, 5, None)
                assert fallback
                chunks = (await session.execute(select(KnowledgeChunk))).scalars().all()
            points = await app.state.index_service.vectors.search(
                await FakeEmbedding().embed("GraphRAG"),
                "00000000-0000-0000-0000-000000000001",
                5,
                {chunk.vector_id for chunk in chunks},
            )
            assert points == []
            await client.post(f"/api/v1/research/{job_id}/index")
            await asyncio.gather(*app.state.index_service.tasks)
            second = (await client.get(f"/api/v1/research/{job_id}/index")).json()
            assert second["documents_skipped"] == 1
            assert second["vector_records"] == status["vector_records"]
            async with app.state.index_service.sessions() as session:
                page = await session.get(CrawledPage, page_id)
                page.text_content = (
                    "BM25 uses inverse document frequency for lexical retrieval. " * 8
                )
                page.title = "BM25 overview"
                await session.commit()
            await client.post(f"/api/v1/research/{job_id}/index")
            await asyncio.gather(*app.state.index_service.tasks)
            changed = (await client.get(f"/api/v1/research/{job_id}/index")).json()
            assert changed["status"] == "COMPLETED", changed
            lexical = (
                await client.post(
                    f"/api/v1/research/{job_id}/retrieve",
                    json={
                        "query": "BM25 inverse document frequency",
                        "retrieval_mode": "lexical",
                        "rerank": False,
                    },
                )
            ).json()
            assert lexical["results"]
            stale = (
                await client.post(
                    f"/api/v1/research/{job_id}/retrieve",
                    json={"query": "GraphRAG", "retrieval_mode": "lexical", "rerank": False},
                )
            ).json()
            assert stale["results"] == []
            consistency = (await client.get(f"/api/v1/research/{job_id}/index/consistency")).json()
            assert consistency["consistent"], consistency
            async with app.state.index_service.sessions() as session:
                count = (
                    await session.execute(select(func.count()).select_from(KnowledgeChunk))
                ).scalar_one()
                fts = (
                    await session.execute(text("SELECT count(*) FROM knowledge_chunks_fts"))
                ).scalar_one()
                assert count == fts == changed["vector_records"]

            # The same passage on another page reuses a point but keeps both sources.
            async with app.state.index_service.sessions() as session:
                sub = (
                    await session.execute(
                        select(ResearchSubquestion).where(
                            ResearchSubquestion.research_job_id == job_id
                        )
                    )
                ).scalar_one()
                original = await session.get(CrawledPage, page_id)
                crawl2 = CrawlJob(
                    start_url="https://example.org/b",
                    max_depth=0,
                    max_pages=1,
                    timeout=10,
                    max_content_size=100000,
                )
                result2 = ResearchSearchResult(
                    research_job_id=job_id,
                    normalized_url="https://example.org/b",
                    url="https://example.org/b",
                    title="BM25 overview",
                    snippet="BM25",
                    domain="example.org",
                    provider="fixture",
                    best_rank=1,
                )
                session.add_all([crawl2, result2])
                await session.flush()
                page2 = CrawledPage(
                    crawl_job_id=crawl2.id,
                    url="https://example.org/b",
                    normalized_url="https://example.org/b",
                    depth=0,
                    title=original.title,
                    text_content=original.text_content,
                    crawl_status="COMPLETED",
                )
                session.add(page2)
                await session.flush()
                page2_id = page2.id
                session.add(
                    ResearchSeed(
                        research_job_id=job_id,
                        subquestion_id=sub.id,
                        result_id=result2.id,
                        semantic_relevance=0.9,
                        seed_score=0.9,
                        selected=True,
                        crawl_job_id=crawl2.id,
                    )
                )
                await session.commit()
            await client.post(f"/api/v1/research/{job_id}/index")
            await asyncio.gather(*app.state.index_service.tasks)
            duplicated = (await client.get(f"/api/v1/research/{job_id}/index")).json()
            assert duplicated["status"] == "COMPLETED", duplicated
            assert duplicated["vector_records"] == changed["vector_records"]
            assert duplicated["chunks_deduplicated"] > 0

            async with app.state.index_service.sessions() as session:
                original = await session.get(CrawledPage, page_id)
                original.title = "GraphRAG again"
                original.text_content = "GraphRAG traverses entity edges for evidence. " * 8
                await session.commit()
            await client.post(f"/api/v1/research/{job_id}/index")
            await asyncio.gather(*app.state.index_service.tasks)
            async with app.state.index_service.sessions() as session:
                doc2 = (
                    await session.execute(
                        select(KnowledgeDocument).where(
                            KnowledgeDocument.crawled_page_id == page2_id
                        )
                    )
                ).scalar_one()
                shared = (
                    (
                        await session.execute(
                            select(KnowledgeChunk)
                            .join(
                                KnowledgeChunkSource,
                                KnowledgeChunkSource.chunk_id == KnowledgeChunk.id,
                            )
                            .where(KnowledgeChunkSource.document_id == doc2.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert shared and all(chunk.document_id == doc2.id for chunk in shared)
            bm25 = (
                await client.post(
                    f"/api/v1/research/{job_id}/retrieve",
                    json={"query": "BM25", "retrieval_mode": "lexical", "rerank": False},
                )
            ).json()
            assert bm25["results"][0]["source_url"] == "https://example.org/b"
            async with app.state.index_service.sessions() as session:
                page2 = await session.get(CrawledPage, page2_id)
                page2.crawl_status = "FAILED"
                await session.commit()
            await client.post(f"/api/v1/research/{job_id}/index")
            await asyncio.gather(*app.state.index_service.tasks)
            removed = (
                await client.post(
                    f"/api/v1/research/{job_id}/retrieve",
                    json={"query": "BM25", "retrieval_mode": "lexical", "rerank": False},
                )
            ).json()
            assert removed["results"] == []
            final_consistency = (
                await client.get(f"/api/v1/research/{job_id}/index/consistency")
            ).json()
            assert final_consistency["consistent"], final_consistency
