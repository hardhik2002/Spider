"""FTS5 lexical index. Triggers maintain the external-content table."""

import math
import re
from collections import Counter

from sqlalchemy import bindparam, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession


def safe_fts_query(query: str) -> str:
    tokens = re.findall(r"[\w]+(?:[-.][\w]+)*", query, flags=re.UNICODE)
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens[:32])


async def search(
    session: AsyncSession,
    query: str,
    research_job_id: str,
    top_k: int,
    allowed_ids: set[int] | None = None,
) -> list[tuple[int, float]]:
    expression = safe_fts_query(query)
    if not expression or allowed_ids == set():
        return []
    statement = text("""SELECT c.id, bm25(knowledge_chunks_fts, 2.0, 1.5, 1.0) AS score
                FROM knowledge_chunks_fts
                JOIN knowledge_chunks c ON c.id = knowledge_chunks_fts.rowid
                WHERE knowledge_chunks_fts MATCH :query AND c.research_job_id = :job_id
                AND (:unfiltered = 1 OR c.id IN :allowed_ids)
                ORDER BY score ASC LIMIT :limit""").bindparams(
        bindparam("allowed_ids", expanding=True)
    )
    ordered_ids = sorted(allowed_ids) if allowed_ids is not None else None
    groups = (
        [None]
        if ordered_ids is None
        else [ordered_ids[start : start + 900] for start in range(0, len(ordered_ids), 900)]
    )
    candidates = []
    try:
        for group in groups:
            rows = await session.execute(
                statement,
                {
                    "query": expression,
                    "job_id": research_job_id,
                    "limit": top_k,
                    "unfiltered": int(group is None),
                    "allowed_ids": group or [],
                },
            )
            candidates.extend((int(row.id), float(row.score)) for row in rows)
    except OperationalError as exc:
        if "no such table: knowledge_chunks_fts" not in str(exc).lower():
            raise
        return await fallback_bm25(session, query, research_job_id, top_k, allowed_ids)
    return sorted(candidates, key=lambda item: (item[1], item[0]))[:top_k]


async def fallback_bm25(
    session: AsyncSession,
    query: str,
    research_job_id: str,
    top_k: int,
    allowed_ids: set[int] | None,
) -> list[tuple[int, float]]:
    """Portable in-memory BM25 for Python SQLite builds without FTS5."""
    terms = [term.lower() for term in re.findall(r"[\w]+(?:[-.][\w]+)*", query)[:32]]
    if not terms:
        return []
    rows = (
        await session.execute(
            text(
                "SELECT id, source_title, heading, text FROM knowledge_chunks "
                "WHERE research_job_id = :job_id"
            ),
            {"job_id": research_job_id},
        )
    ).all()
    corpus = []
    frequencies: Counter = Counter()
    for row in rows:
        if allowed_ids is not None and row.id not in allowed_ids:
            continue
        weighted = (
            (str(row.source_title or "") + " ") * 2 + (str(row.heading or "") + " ") + str(row.text)
        )
        bag = Counter(token.lower() for token in re.findall(r"[\w]+(?:[-.][\w]+)*", weighted))
        corpus.append((int(row.id), bag, sum(bag.values())))
        frequencies.update(set(bag))
    if not corpus:
        return []
    mean_length = sum(length for _, _, length in corpus) / len(corpus)
    scores = []
    for chunk_id, bag, length in corpus:
        score = 0.0
        for term in set(terms):
            tf = bag[term]
            if tf:
                idf = math.log(
                    1 + (len(corpus) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
                )
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / mean_length))
        if score:
            scores.append((chunk_id, -score))
    return sorted(scores, key=lambda item: (item[1], item[0]))[:top_k]
