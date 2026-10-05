"""Idempotent additive Phase 2 migration for existing Phase 1 SQLite files."""

from sqlalchemy.ext.asyncio import AsyncConnection

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
    await connection.exec_driver_sql("PRAGMA user_version = 2")
