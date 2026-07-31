"""Hybrid retrieval: dense + BM25, fused with RRF, then reranked.

Each stage can be switched off so the evaluation harness can attribute a change
in the metrics to the stage that caused it.
"""

import re
import uuid
from dataclasses import dataclass, field, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import ChunkParent, Source
from app.rag.dense import search_dense
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.rerank import get_reranker
from app.rag.sparse import search_sparse
from app.services import usage
from app.services.providers import embeddings


@dataclass
class RetrievedChunk:
    """Generation-sized parent section with stable source/citation identity.

    Dense and sparse retrieval rank embedded child passages, but callers receive
    their parent section so an answer sees enough context without losing the page,
    heading or source metadata needed to render a citation.
    """

    chunk_id: str
    source_id: str
    source_title: str
    heading: str | None
    content: str
    score: float
    images: list[dict[str, str]] = field(default_factory=list)
    source_type: str | None = None
    source_origin: str | None = None
    source_position: int | None = None
    source_url: str | None = None
    page_start: int | None = None
    page_end: int | None = None


@dataclass
class RetrievalConfig:
    """Independent stage switches used by production retrieval and RAG ablations."""

    use_dense: bool = True
    use_sparse: bool = True
    use_rerank: bool = True
    top_k: int = settings.retrieval_top_k
    candidates: int = settings.retrieval_candidates


async def retrieve(
    workspace_id: uuid.UUID,
    query: str,
    config: RetrievalConfig | None = None,
) -> list[RetrievedChunk]:
    """Owns its database session so reranking never holds a pooled connection.

    The reranker is a network call; keeping a transaction open across it would
    exhaust the pool under concurrency.
    """
    config = config or RetrievalConfig()
    async with AsyncSessionLocal() as db:
        candidates = await _candidates(db, workspace_id, query, config)

    if not candidates:
        return []
    if not config.use_rerank:
        return candidates[: config.top_k]

    ranked = await get_reranker().rank(query, [c.content for c in candidates], config.top_k)
    return [replace(candidates[r.index], score=r.score) for r in ranked]


async def _candidates(
    db: AsyncSession, workspace_id: uuid.UUID, query: str, config: RetrievalConfig
) -> list[RetrievedChunk]:
    rankings: list[list[uuid.UUID]] = []
    if config.use_dense:
        usage.record_embedding("embed_query", query)
        vector = await embeddings().aembed_query(query)
        rankings.append(await search_dense(db, workspace_id, vector, config.candidates))
    if config.use_sparse:
        hits = await search_sparse(db, workspace_id, query, config.candidates)
        rankings.append([h.parent_id for h in hits])

    if not rankings:
        return []

    fused = reciprocal_rank_fusion(rankings)[: config.candidates]
    if not fused:
        return []

    parents = await _load(db, [pid for pid, _ in fused])
    return _as_chunks(
        [parents[pid] for pid, _ in fused if pid in parents],
        [score for pid, score in fused if pid in parents],
    )


async def _load(
    db: AsyncSession, parent_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[ChunkParent, Source]]:
    rows = await db.execute(
        select(ChunkParent, Source)
        .join(Source, ChunkParent.source_id == Source.id)
        .where(ChunkParent.id.in_(parent_ids))
    )
    return {parent.id: (parent, source) for parent, source in rows}


def _as_chunks(
    ordered: list[tuple[ChunkParent, Source]], scores: list[float]
) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=str(parent.id),
            source_id=str(parent.source_id),
            source_title=source.title,
            heading=parent.heading_path,
            content=parent.content,
            score=score,
            images=parent.images or [],
            source_type=source.type.value,
            source_origin=source.origin,
            source_position=parent.position,
            source_url=_page_url(parent.content) or source.origin,
            page_start=parent.page_start,
            page_end=parent.page_end,
        )
        for (parent, source), score in zip(ordered, scores, strict=False)
    ]


def _page_url(content: str) -> str | None:
    match = re.search(r"(?:^|\n)Source:\s*(https?://\S+)", content)
    return match.group(1).rstrip(").,;") if match else None
