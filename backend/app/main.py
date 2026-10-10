import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.agent.llm import OllamaAgentLLM
from app.agent.service import AgentService
from app.api.routes import agent, crawl, evidence, health, rag, research
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.crawler.embedding import EmbeddingProvider
from app.db.database import initialize_database, make_engine, make_session_factory
from app.evidence.llm import OllamaEvidenceLLM
from app.evidence.nli import TransformersNLIClassifier
from app.evidence.service import EvidenceEngineService
from app.rag.chunking import Tokenizer
from app.rag.indexing import IndexingService
from app.rag.reranker import CrossEncoderReranker, Reranker
from app.rag.retrieval import RetrievalService
from app.rag.vector import QdrantVectorIndex, VectorIndex
from app.research.planner import OllamaResearchPlanner, ResearchPlanner
from app.research.search import DDGSSearchProvider, SearchProvider
from app.services.crawl_service import CrawlService
from app.services.research_service import ResearchService


def create_app(
    settings: Settings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    research_planner: ResearchPlanner | None = None,
    search_provider: SearchProvider | None = None,
    vector_index: VectorIndex | None = None,
    reranker: Reranker | None = None,
    tokenizer: Tokenizer | None = None,
    agent_assessor=None,
    gap_query_generator=None,
    evidence_extractor=None,
    evidence_equivalence=None,
    evidence_counterqueries=None,
    evidence_adjudicator=None,
    evidence_classifier=None,
) -> FastAPI:
    config = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        engine = make_engine(config.database_url)
        await initialize_database(engine)
        session_factory = make_session_factory(engine)
        async with httpx.AsyncClient(
            headers={"User-Agent": config.user_agent},
            follow_redirects=False,
            trust_env=False,
        ) as client:
            service = CrawlService(
                session_factory, config, client, embedding_provider=embedding_provider
            )
            await service.repository.recover_jobs()
            research_service = ResearchService(
                session_factory,
                config,
                service,
                research_planner
                or OllamaResearchPlanner(
                    config.ollama_model, config.ollama_url, config.planner_timeout_seconds
                ),
                search_provider or DDGSSearchProvider(config.search_retries),
            )
            await research_service.recover_jobs()
            vectors = vector_index or QdrantVectorIndex(
                config.qdrant_path, config.qdrant_collection
            )
            index_service = IndexingService(
                session_factory, config, service.embedding_provider, vectors, tokenizer
            )
            await index_service.recover_jobs()
            retrieval_service = RetrievalService(
                session_factory,
                config,
                service.embedding_provider,
                vectors,
                reranker
                or CrossEncoderReranker(
                    config.reranker_model, config.reranker_device, config.reranker_batch_size
                ),
            )
            Path(config.agent_checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
            async with AsyncSqliteSaver.from_conn_string(config.agent_checkpoint_path) as saver:
                llm = OllamaAgentLLM(
                    config.agent_model or config.ollama_model,
                    config.ollama_url,
                    config.planner_timeout_seconds,
                    config.agent_temperature,
                )
                agent_service = AgentService(
                    session_factory,
                    config,
                    research_service,
                    service,
                    index_service,
                    retrieval_service,
                    agent_assessor or llm,
                    gap_query_generator or llm,
                    saver,
                )
                await agent_service.recover_jobs()
                evidence_llm = OllamaEvidenceLLM(
                    config.agent_model or config.ollama_model,
                    config.ollama_url,
                    config.planner_timeout_seconds,
                    0,
                )
                evidence_service = EvidenceEngineService(
                    session_factory,
                    config,
                    retrieval_service,
                    evidence_classifier
                    or TransformersNLIClassifier(
                        config.evidence_nli_model,
                        config.evidence_nli_batch_size,
                        config.evidence_nli_device,
                        config.evidence_nli_fallback_model,
                    ),
                    evidence_extractor or evidence_llm,
                    evidence_equivalence or evidence_llm,
                    evidence_counterqueries or evidence_llm,
                    evidence_adjudicator or evidence_llm,
                )
                await evidence_service.recover_jobs()
                app.state.db_engine = engine
                app.state.crawl_service = service
                app.state.research_service = research_service
                app.state.index_service = index_service
                app.state.retrieval_service = retrieval_service
                app.state.agent_service = agent_service
                app.state.evidence_service = evidence_service
                try:
                    yield
                finally:
                    await evidence_service.shutdown()
                    await agent_service.shutdown()
                    await research_service.shutdown()
                    await index_service.shutdown()
                    await service.shutdown()
                    await vectors.close()
                    await engine.dispose()

    app = FastAPI(title="SpiderMind", version="0.6.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(crawl.router)
    app.include_router(research.router)
    app.include_router(rag.router)
    app.include_router(agent.router)
    app.include_router(evidence.router)
    return app


app = create_app()
