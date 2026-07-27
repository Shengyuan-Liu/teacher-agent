"""BM25 lexical retrieval.

Dense vectors miss exact identifiers — a query for "FLM-419" or "Kaczmarz"
embeds to something merely topical, while BM25 matches the token itself.

The index is built per workspace and cached; it is rebuilt whenever ingestion
changes the workspace. Corpora here are thousands of chunks, so an in-process
index is cheap. Move this to Postgres full-text search if a workspace ever
grows past that.
"""

import re
import uuid
from dataclasses import dataclass

from rank_bm25 import BM25Okapi
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk

TOKEN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN.findall(text)]


@dataclass
class SparseHit:
    parent_id: uuid.UUID
    score: float


@dataclass
class _Index:
    signature: tuple[int, str]
    bm25: BM25Okapi
    parent_ids: list[uuid.UUID]


_cache: dict[uuid.UUID, _Index] = {}


async def _signature(db: AsyncSession, workspace_id: uuid.UUID) -> tuple[int, str]:
    """Cheap fingerprint of the workspace corpus, to spot re-ingestion."""
    row = await db.execute(
        select(func.count(Chunk.id), func.max(cast(Chunk.id, String))).where(
            Chunk.workspace_id == workspace_id
        )
    )
    count, marker = row.one()
    return count, str(marker)


async def _index_for(db: AsyncSession, workspace_id: uuid.UUID) -> _Index | None:
    signature = await _signature(db, workspace_id)
    cached = _cache.get(workspace_id)
    if cached and cached.signature == signature:
        return cached

    rows = await db.execute(
        select(Chunk.parent_id, Chunk.content).where(Chunk.workspace_id == workspace_id)
    )
    parent_ids: list[uuid.UUID] = []
    corpus: list[list[str]] = []
    for parent_id, content in rows:
        parent_ids.append(parent_id)
        corpus.append(tokenize(content))
    if not corpus:
        return None

    index = _Index(signature=signature, bm25=BM25Okapi(corpus), parent_ids=parent_ids)
    _cache[workspace_id] = index
    return index


async def search_sparse(
    db: AsyncSession, workspace_id: uuid.UUID, query: str, limit: int
) -> list[SparseHit]:
    index = await _index_for(db, workspace_id)
    tokens = tokenize(query)
    if index is None or not tokens:
        return []

    scores = index.bm25.get_scores(tokens)

    best: dict[uuid.UUID, float] = {}
    for parent_id, score in zip(index.parent_ids, scores, strict=True):
        if score > best.get(parent_id, 0.0):
            best[parent_id] = float(score)

    ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
    return [SparseHit(parent_id=pid, score=score) for pid, score in ranked[:limit] if score > 0]


def clear_cache() -> None:
    _cache.clear()
