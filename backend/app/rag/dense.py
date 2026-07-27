"""Dense vector retrieval over child chunks, resolved to their parents."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk


async def search_dense(
    db: AsyncSession, workspace_id: uuid.UUID, query_vector: list[float], limit: int
) -> list[uuid.UUID]:
    """Parent ids ordered by their best-matching child, de-duplicated."""
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    rows = await db.execute(
        select(Chunk.parent_id, distance)
        .where(Chunk.workspace_id == workspace_id)
        .order_by(distance)
        .limit(limit * 4)
    )

    ordered: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for parent_id, _ in rows:
        if parent_id not in seen:
            seen.add(parent_id)
            ordered.append(parent_id)
        if len(ordered) == limit:
            break
    return ordered
