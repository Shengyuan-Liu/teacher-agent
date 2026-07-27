import uuid
from typing import Any

import structlog
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal
from app.models import Chunk, ChunkParent, Source, SourceStatus, SourceType
from app.rag.chunking import chunk_document
from app.rag.extract import extract_text
from app.rag.pdf_convert import get_converter
from app.services.providers import embeddings
from app.services.storage import save_image

log = structlog.get_logger()

EMBED_BATCH = 64


async def ingest_source(ctx: dict[str, Any], source_id: str) -> None:
    async with AsyncSessionLocal() as db:
        source = await db.get(Source, uuid.UUID(source_id))
        if source is None:
            log.warning("ingest.source_gone", source_id=source_id)
            return
        try:
            await _run(db, source)
        except Exception as exc:
            source.status = SourceStatus.FAILED
            source.error = str(exc)[:2000]
            await db.commit()
            log.error("ingest.failed", source_id=source_id, error=str(exc))
            raise


async def _run(db, source: Source) -> None:
    source.status = SourceStatus.PARSING
    source.error = None
    await db.commit()

    images: dict[str, str] = {}
    if source.type is SourceType.PDF:
        converted = await get_converter().convert(source.file_path)
        text = converted.markdown
        for image_id, data in converted.images.items():
            images[image_id] = str(save_image(source.workspace_id, source.id, image_id, data))
    else:
        text = extract_text(source.file_path, source.type)

    parents = chunk_document(text)
    if not parents:
        raise ValueError("No text could be extracted from this file")

    source.status = SourceStatus.EMBEDDING
    await db.commit()

    # Re-ingest replaces the old tree; children cascade from their parents.
    await db.execute(delete(ChunkParent).where(ChunkParent.source_id == source.id))
    await db.execute(delete(Chunk).where(Chunk.source_id == source.id))

    pending: list[Chunk] = []
    for index, parent in enumerate(parents):
        row = ChunkParent(
            source_id=source.id,
            workspace_id=source.workspace_id,
            position=index,
            heading_path=parent.heading_path,
            content=parent.content,
            images=[{"id": i, "path": images[i]} for i in parent.image_ids if i in images] or None,
        )
        db.add(row)
        await db.flush()
        for offset, child in enumerate(parent.children):
            pending.append(
                Chunk(
                    parent_id=row.id,
                    source_id=source.id,
                    workspace_id=source.workspace_id,
                    position=offset,
                    heading=parent.heading_path,
                    content=child,
                    embedding=[],
                )
            )

    for start in range(0, len(pending), EMBED_BATCH):
        batch = pending[start : start + EMBED_BATCH]
        vectors = await embeddings().aembed_documents([c.content for c in batch])
        for chunk, vector in zip(batch, vectors, strict=True):
            chunk.embedding = vector
            db.add(chunk)

    source.status = SourceStatus.READY
    await db.commit()
    log.info(
        "ingest.done",
        source_id=str(source.id),
        parents=len(parents),
        children=len(pending),
        images=len(images),
    )
