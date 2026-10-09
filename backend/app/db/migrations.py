"""Idempotent additive migrations for existing SQLite files."""

import logging

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger("spidermind.migrations")

ADDITIONS: dict[str, dict[str, str]] = {
    "crawl_jobs": {
        "research_query": "TEXT",
        "crawl_mode": "VARCHAR(20) NOT NULL DEFAULT 'fifo'",
        "min_relevance_score": "FLOAT",
        "embedding_model": "TEXT",
        "depth_penalty": "FLOAT",
        "exploration_rate": "FLOAT",
        "links_scored": "INTEGER NOT NULL DEFAULT 0",
        "links_below_threshold": "INTEGER NOT NULL DEFAULT 0",
        "relevance_score_sum": "FLOAT NOT NULL DEFAULT 0",
        "highest_relevance_score": "FLOAT",
        "query_embedding_ms": "INTEGER",
        "candidate_embeddings": "INTEGER NOT NULL DEFAULT 0",
        "candidate_scoring_ms": "INTEGER NOT NULL DEFAULT 0",
        "model_load_ms": "INTEGER",
        "duration_ms": "INTEGER",
    },
    "crawled_pages": {
        "predicted_link_relevance": "FLOAT",
        "actual_page_relevance": "FLOAT",
        "priority_score": "FLOAT",
        "discovery_order": "INTEGER",
        "source_link_id": "INTEGER",
    },
    "discovered_links": {
        "source_page_title": "TEXT",
        "link_context": "TEXT",
        "target_depth": "INTEGER",
        "discovery_order": "INTEGER",
        "relevance_score": "FLOAT",
        "priority_score": "FLOAT",
        "depth_penalty": "FLOAT",
        "scoring_status": "VARCHAR(20) NOT NULL DEFAULT 'NOT_SCORED'",
        "scoring_reason": "TEXT",
        "selected_for_crawl": "BOOLEAN NOT NULL DEFAULT 0",
        "rejection_reason": "VARCHAR(40)",
    },
    "knowledge_documents": {
        "chunking_profile": "TEXT",
        "embedding_model": "TEXT",
    },
    "research_search_queries": {
        "origin": "VARCHAR(20) NOT NULL DEFAULT 'PLAN'",
        "agent_run_id": "VARCHAR(36)",
        "gap_id": "VARCHAR(36)",
        "agent_iteration": "INTEGER",
    },
}


async def migrate_sqlite(connection: AsyncConnection) -> None:
    for table, columns in ADDITIONS.items():
        rows = await connection.exec_driver_sql(f"PRAGMA table_info({table})")
        existing = {row[1] for row in rows}
        for column, definition in columns.items():
            if column not in existing:
                await connection.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )
    existing_fts = await connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE name = 'knowledge_chunks_fts'"
    )
    needs_rebuild = existing_fts.first() is None
    try:
        await connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING "
            "fts5(source_title, heading, text, content='knowledge_chunks', content_rowid='id')"
        )
    except OperationalError as exc:
        if "no such module: fts5" not in str(exc).lower():
            raise
        logger.warning("SQLite FTS5 unavailable; using local BM25 fallback")
        await connection.exec_driver_sql("PRAGMA user_version = 5")
        return
    await connection.exec_driver_sql(
        """CREATE TRIGGER IF NOT EXISTS knowledge_chunks_ai AFTER INSERT ON knowledge_chunks BEGIN
        INSERT INTO knowledge_chunks_fts(rowid, source_title, heading, text)
        VALUES (new.id, new.source_title, new.heading, new.text); END"""
    )
    await connection.exec_driver_sql(
        """CREATE TRIGGER IF NOT EXISTS knowledge_chunks_ad AFTER DELETE ON knowledge_chunks BEGIN
        INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, source_title, heading, text)
        VALUES ('delete', old.id, old.source_title, old.heading, old.text); END"""
    )
    await connection.exec_driver_sql(
        """CREATE TRIGGER IF NOT EXISTS knowledge_chunks_au AFTER UPDATE ON knowledge_chunks BEGIN
        INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, source_title, heading, text)
        VALUES ('delete', old.id, old.source_title, old.heading, old.text);
        INSERT INTO knowledge_chunks_fts(rowid, source_title, heading, text)
        VALUES (new.id, new.source_title, new.heading, new.text); END"""
    )
    if needs_rebuild:
        await connection.exec_driver_sql(
            "INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts) VALUES('rebuild')"
        )
    await connection.exec_driver_sql("PRAGMA user_version = 5")
