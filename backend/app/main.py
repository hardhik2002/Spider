from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.routes import crawl, health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.crawler.embedding import EmbeddingProvider
from app.db.database import initialize_database, make_engine, make_session_factory
from app.services.crawl_service import CrawlService


def create_app(
    settings: Settings | None = None, embedding_provider: EmbeddingProvider | None = None
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
            app.state.db_engine = engine
            app.state.crawl_service = service
            try:
                yield
            finally:
                await service.shutdown()
                await engine.dispose()

    app = FastAPI(title="SpiderMind", version="0.2.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(crawl.router)
    return app


app = create_app()
