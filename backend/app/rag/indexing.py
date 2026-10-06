"""Incremental research-job indexing with persistent progress and repair."""

import asyncio
import hashlib
import logging
import statistics
import time
from collections import Counter
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.crawler.embedding import EmbeddingProvider
from app.db.models import CrawledPage, ResearchJob, ResearchSeed, utc_now
from app.rag.chunking import BGETokenizer, Tokenizer, chunk_text
from app.rag.models import (
    IndexDocumentStatus,
    IndexJob,
    KnowledgeChunk,
    KnowledgeChunkSource,
    KnowledgeDocument,
)
from app.rag.vector import VectorIndex

logger = logging.getLogger("spidermind.rag.indexing")


def chunk_representation(title: str | None, heading: str | None, passage: str) -> str:
    return f"Title: {title or ''}\nSection: {heading or ''}\n\nPassage:\n{passage}"


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class IndexingService:
    def __init__(
        self,
        sessions: async_sessionmaker,
        settings: Settings,
        embedder: EmbeddingProvider,
        vectors: VectorIndex,
        tokenizer: Tokenizer | None = None,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.embedder = embedder
        self.vectors = vectors
        self.tokenizer = tokenizer or BGETokenizer(settings.embedding_model_name)
        self.tasks: set[asyncio.Task] = set()
        self.locks: dict[str, asyncio.Lock] = {}

    def profile(self) -> str:
        s = self.settings
        return (
            f"target={s.chunk_target_tokens},max={s.chunk_max_tokens},"
            f"overlap={s.chunk_overlap_tokens},min={s.chunk_min_tokens}"
        )

    async def start(self, research_job_id: str) -> IndexJob:
        async with self.sessions() as session:
            if await session.get(ResearchJob, research_job_id) is None:
                raise KeyError(research_job_id)
            active = (
                (
                    await session.execute(
                        select(IndexJob)
                        .where(
                            IndexJob.research_job_id == research_job_id,
                            IndexJob.status.in_(["PENDING", "RUNNING"]),
                        )
                        .order_by(IndexJob.created_at.desc())
                    )
                )
                .scalars()
                .first()
            )
            if active:
                return active
            job = IndexJob(
                research_job_id=research_job_id,
                embedding_model=self.embedder.model_name,
                chunking_profile=self.profile(),
            )
            session.add(job)
            await session.commit()
        task = asyncio.create_task(self.run(job.id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job

    async def recover_jobs(self) -> None:
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(IndexJob).where(IndexJob.status.in_(["PENDING", "RUNNING"]))
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.status = "PARTIAL"
                row.completed_at = utc_now()
                row.error_message = "Interrupted by process restart; start indexing again to resume"
            await session.commit()

    async def _pages(self, session, research_job_id: str) -> list[CrawledPage]:
        return (
            (
                await session.execute(
                    select(CrawledPage)
                    .join(ResearchSeed, ResearchSeed.crawl_job_id == CrawledPage.crawl_job_id)
                    .where(
                        ResearchSeed.research_job_id == research_job_id,
                        ResearchSeed.selected.is_(True),
                        CrawledPage.crawl_status == "COMPLETED",
                        CrawledPage.duplicate_of_page_id.is_(None),
                        CrawledPage.text_content.is_not(None),
                    )
                    .distinct()
                    .order_by(CrawledPage.id)
                )
            )
            .scalars()
            .all()
        )

    async def _progress(self, job_id: str, **values) -> None:
        async with self.sessions() as session:
            await session.execute(update(IndexJob).where(IndexJob.id == job_id).values(**values))
            await session.commit()

    async def run(self, job_id: str) -> None:
        started = time.monotonic()
        async with self.sessions() as session:
            job = await session.get(IndexJob, job_id)
            research_job_id = job.research_job_id
        lock = self.locks.setdefault(research_job_id, asyncio.Lock())
        async with lock:
            counts: Counter = Counter()
            token_counts: list[int] = []
            try:
                await self._progress(job_id, status="RUNNING", started_at=utc_now())
                async with self.sessions() as session:
                    pages = await self._pages(session, research_job_id)
                pages = [p for p in pages if p.text_content and p.text_content.strip()]
                await self._progress(job_id, documents_discovered=len(pages))
                for page in pages:
                    try:
                        result = await self._index_page(job_id, research_job_id, page)
                        counts.update(result[0])
                        token_counts.extend(result[1])
                        counts["documents_processed"] += 1
                        await self._document_status(job_id, page.id, result[2], result[3])
                    except Exception as exc:
                        logger.exception("index document %s failed", page.id)
                        counts["documents_failed"] += 1
                        counts["documents_processed"] += 1
                        await self._document_status(job_id, page.id, "FAILED", 0, str(exc)[:2000])
                    await self._progress(job_id, **dict(counts))
                await self._mark_removed(research_job_id, {page.id for page in pages})
                await self._prune_orphans(research_job_id)
                vector_records = await self.vectors.count(research_job_id)
                async with self.sessions() as session:
                    lexical_records = (
                        await session.execute(
                            select(func.count())
                            .select_from(KnowledgeChunk)
                            .where(KnowledgeChunk.research_job_id == research_job_id)
                        )
                    ).scalar_one()
                status = (
                    "COMPLETED"
                    if counts["documents_failed"] == 0
                    else (
                        "PARTIAL"
                        if counts["documents_indexed"] or counts["documents_skipped"]
                        else "FAILED"
                    )
                )
                await self._progress(
                    job_id,
                    status=status,
                    completed_at=utc_now(),
                    vector_records=vector_records,
                    lexical_records=lexical_records,
                    average_chunk_tokens=statistics.mean(token_counts) if token_counts else None,
                    median_chunk_tokens=statistics.median(token_counts) if token_counts else None,
                    max_chunk_tokens=max(token_counts, default=None),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            except Exception as exc:
                logger.exception("index job %s failed", job_id)
                await self._progress(
                    job_id,
                    status="FAILED",
                    completed_at=utc_now(),
                    error_message=str(exc)[:2000],
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

    async def _document_status(
        self, job_id: str, page_id: int, status: str, count: int, error: str | None = None
    ) -> None:
        async with self.sessions() as session:
            document = (
                await session.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.crawled_page_id == page_id,
                        KnowledgeDocument.research_job_id
                        == (
                            select(IndexJob.research_job_id)
                            .where(IndexJob.id == job_id)
                            .scalar_subquery()
                        ),
                    )
                )
            ).scalar_one_or_none()
            session.add(
                IndexDocumentStatus(
                    index_job_id=job_id,
                    crawled_page_id=page_id,
                    document_id=document.id if document else None,
                    status=status,
                    chunk_count=count,
                    error_message=error,
                )
            )
            await session.commit()

    async def _index_page(self, job_id: str, research_job_id: str, page: CrawledPage):
        content_hash = fingerprint((page.title or "") + "\n" + (page.text_content or ""))
        source_url = page.final_url or page.normalized_url
        domain = (urlsplit(source_url).hostname or "").lower()
        async with self.sessions() as session:
            document = (
                await session.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.research_job_id == research_job_id,
                        KnowledgeDocument.crawled_page_id == page.id,
                    )
                )
            ).scalar_one_or_none()
            if (
                document
                and document.content_hash == content_hash
                and document.index_status == "COMPLETED"
                and document.chunking_profile == self.profile()
                and document.embedding_model == self.embedder.model_name
            ):
                rows = (
                    (
                        await session.execute(
                            select(KnowledgeChunk)
                            .join(
                                KnowledgeChunkSource,
                                KnowledgeChunkSource.chunk_id == KnowledgeChunk.id,
                            )
                            .where(KnowledgeChunkSource.document_id == document.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                existing = await self.vectors.existing([row.vector_id for row in rows])
                if len(existing) == len({row.vector_id for row in rows}):
                    return (
                        Counter(documents_skipped=1, chunks_skipped=len(rows)),
                        [],
                        "SKIPPED",
                        len(rows),
                    )

        passages = chunk_text(
            page.text_content or "",
            self.tokenizer,
            title=page.title,
            target_tokens=self.settings.chunk_target_tokens,
            max_tokens=self.settings.chunk_max_tokens,
            overlap_tokens=self.settings.chunk_overlap_tokens,
            min_tokens=self.settings.chunk_min_tokens,
        )
        if not passages:
            raise ValueError("Document produced no passages")
        new_chunks: list[KnowledgeChunk] = []
        links: list[KnowledgeChunkSource] = []
        dedup = 0
        fts_started = time.monotonic()
        async with self.sessions() as session:
            document = (
                await session.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.research_job_id == research_job_id,
                        KnowledgeDocument.crawled_page_id == page.id,
                    )
                )
            ).scalar_one_or_none()
            if document is None:
                document = KnowledgeDocument(
                    research_job_id=research_job_id,
                    crawled_page_id=page.id,
                    crawl_job_id=page.crawl_job_id,
                    source_url=source_url,
                    canonical_url=page.canonical_url,
                    title=page.title,
                    meta_description=page.meta_description,
                    content_hash=content_hash,
                    source_domain=domain,
                )
                session.add(document)
                await session.flush()
            else:
                await session.execute(
                    delete(KnowledgeChunkSource).where(
                        KnowledgeChunkSource.document_id == document.id
                    )
                )
            document.content_hash = content_hash
            force_reembed = document.embedding_model not in (None, self.embedder.model_name)
            document.chunking_profile = self.profile()
            document.embedding_model = self.embedder.model_name
            document.title = page.title
            document.source_url = source_url
            document.source_domain = domain
            document.index_status = "RUNNING"
            document.error_message = None
            for passage in passages:
                representation = chunk_representation(page.title, passage.heading, passage.text)
                text_hash = fingerprint(representation)
                chunk = (
                    await session.execute(
                        select(KnowledgeChunk).where(
                            KnowledgeChunk.research_job_id == research_job_id,
                            KnowledgeChunk.text_hash == text_hash,
                        )
                    )
                ).scalar_one_or_none()
                if chunk is None:
                    chunk = KnowledgeChunk(
                        research_job_id=research_job_id,
                        document_id=document.id,
                        crawl_job_id=page.crawl_job_id,
                        crawled_page_id=page.id,
                        source_url=source_url,
                        source_title=page.title,
                        source_domain=domain,
                        chunk_index=passage.index,
                        text=passage.text,
                        text_hash=text_hash,
                        token_count=passage.token_count,
                        heading=passage.heading,
                        vector_id=str(uuid5(NAMESPACE_URL, research_job_id + ":" + text_hash)),
                    )
                    session.add(chunk)
                    await session.flush()
                    new_chunks.append(chunk)
                else:
                    dedup += 1
                link = KnowledgeChunkSource(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    research_job_id=research_job_id,
                    chunk_index=passage.index,
                    heading=passage.heading,
                )
                session.add(link)
                await session.flush()
                links.append(link)
            for before, after in zip(links, links[1:], strict=False):
                before.next_source_id = after.id
                after.previous_source_id = before.id
            await session.commit()
        fts_ms = int((time.monotonic() - fts_started) * 1000)

        # Repair vectors absent after a prior interrupted write, including reused chunks.
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(KnowledgeChunk)
                        .join(
                            KnowledgeChunkSource, KnowledgeChunkSource.chunk_id == KnowledgeChunk.id
                        )
                        .where(KnowledgeChunkSource.document_id == document.id)
                    )
                )
                .scalars()
                .all()
            )
        unique = {row.vector_id: row for row in rows}
        existing = await self.vectors.existing(list(unique))
        missing = [
            row for vector_id, row in unique.items() if force_reembed or vector_id not in existing
        ]
        embed_started = time.monotonic()
        points = []
        for start in range(0, len(missing), self.settings.embedding_batch_size):
            batch = missing[start : start + self.settings.embedding_batch_size]
            vectors = await self.embedder.embed_many(
                [chunk_representation(row.source_title, row.heading, row.text) for row in batch]
            )
            for row, vector in zip(batch, vectors, strict=True):
                if not vector:
                    raise ValueError("Embedding provider returned an empty vector")
                points.append(
                    (
                        row.vector_id,
                        vector,
                        {
                            "research_job_id": research_job_id,
                            "chunk_id": row.id,
                            "document_id": row.document_id,
                            "crawled_page_id": row.crawled_page_id,
                            "source_url": row.source_url,
                            "source_domain": row.source_domain,
                            "chunk_index": row.chunk_index,
                            "text_hash": row.text_hash,
                        },
                    )
                )
        embed_ms = int((time.monotonic() - embed_started) * 1000)
        upsert_started = time.monotonic()
        await self.vectors.upsert(points)
        upsert_ms = int((time.monotonic() - upsert_started) * 1000)
        async with self.sessions() as session:
            await session.execute(
                update(KnowledgeDocument)
                .where(KnowledgeDocument.id == document.id)
                .values(index_status="COMPLETED", indexed_at=utc_now())
            )
            if missing:
                await session.execute(
                    update(KnowledgeChunk)
                    .where(KnowledgeChunk.id.in_([row.id for row in missing]))
                    .values(
                        embedding_model=self.embedder.model_name,
                        embedding_dimension=len(points[0][1]),
                    )
                )
                await session.execute(
                    update(IndexJob)
                    .where(IndexJob.id == job_id)
                    .values(
                        embedding_dimension=len(points[0][1]),
                        embedding_batches=IndexJob.embedding_batches
                        + (len(missing) + self.settings.embedding_batch_size - 1)
                        // self.settings.embedding_batch_size,
                        embedding_duration_ms=IndexJob.embedding_duration_ms + embed_ms,
                        vector_upsert_duration_ms=IndexJob.vector_upsert_duration_ms + upsert_ms,
                        fts_index_duration_ms=IndexJob.fts_index_duration_ms + fts_ms,
                    )
                )
            await session.commit()
        return (
            Counter(
                documents_indexed=1,
                chunks_created=len(new_chunks),
                chunks_deduplicated=dedup,
                chunks_embedded=len(missing),
            ),
            [p.token_count for p in passages],
            "COMPLETED",
            len(passages),
        )

    async def _mark_removed(self, research_job_id: str, eligible_page_ids: set[int]) -> None:
        async with self.sessions() as session:
            documents = (
                (
                    await session.execute(
                        select(KnowledgeDocument).where(
                            KnowledgeDocument.research_job_id == research_job_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for document in documents:
                if document.crawled_page_id not in eligible_page_ids:
                    await session.execute(
                        delete(KnowledgeChunkSource).where(
                            KnowledgeChunkSource.document_id == document.id
                        )
                    )
                    document.index_status = "REMOVED"
            await session.commit()

    async def _prune_orphans(self, research_job_id: str) -> None:
        async with self.sessions() as session:
            orphans = (
                (
                    await session.execute(
                        select(KnowledgeChunk).where(
                            KnowledgeChunk.research_job_id == research_job_id,
                            ~KnowledgeChunk.id.in_(select(KnowledgeChunkSource.chunk_id)),
                        )
                    )
                )
                .scalars()
                .all()
            )
            ids = [row.vector_id for row in orphans]
            await self.vectors.delete(ids)
            for row in orphans:
                await session.delete(row)
            await session.commit()

    async def shutdown(self) -> None:
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
