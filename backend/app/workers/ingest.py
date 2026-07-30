import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import and_, delete, func, or_, select

from app.agents.outline import build_outline
from app.core.database import AsyncSessionLocal
from app.models import Chunk, ChunkParent, Source, SourceStatus, SourceType
from app.rag.chunking import chunk_document
from app.rag.crawl import crawl, pages_to_markdown
from app.rag.extract import extract_text
from app.rag.pdf_convert import get_converter
from app.rag.repo import repo_to_markdown
from app.services.providers import embeddings
from app.services.storage import save_image
from app.services.workspace_status import ACTIVE_SOURCE_STATUSES, refresh_workspace_status

log = structlog.get_logger()

EMBED_BATCH = 64
PENDING_GRACE_SECONDS = 60

# The OCR call is one request for the whole file, so nothing finer is available
# until chunks exist; embedding is batched and reports real progress.
CONVERTED = 0.35
CHUNKED = 0.45
EMBEDDED = 0.98


async def _refresh_outline(db, workspace_id) -> None:
    """Rebuild the topic map once every source has settled.

    Outline failure must not fail an ingestion that already succeeded; the
    planner regenerates it on demand anyway.
    """
    active = await db.scalar(
        select(func.count(Source.id)).where(
            Source.workspace_id == workspace_id,
            Source.status.in_(ACTIVE_SOURCE_STATUSES),
        )
    )
    if active:
        return
    try:
        await build_outline(workspace_id)
    except Exception as exc:
        log.warning("outline.refresh_failed", workspace_id=str(workspace_id), error=str(exc))


async def _report(db, source: Source, progress: float, detail: str) -> None:
    source.progress = progress
    source.progress_detail = detail
    await db.commit()


async def requeue_interrupted(ctx: dict[str, Any]) -> None:
    """Re-enqueue work a previous worker died in the middle of.

    PARSING and EMBEDDING can only mean a worker vanished mid-job. PENDING is
    ambiguous — it may have been enqueued a moment ago — so only pick it up once
    it has sat there long enough that no live job can be behind it.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=PENDING_GRACE_SECONDS)
    async with AsyncSessionLocal() as db:
        stranded = list(
            await db.scalars(
                select(Source).where(
                    or_(
                        Source.status.in_([SourceStatus.PARSING, SourceStatus.EMBEDDING]),
                        and_(
                            Source.status == SourceStatus.PENDING,
                            Source.updated_at < cutoff,
                        ),
                    )
                )
            )
        )
        for source in stranded:
            source.status = SourceStatus.PENDING
            source.progress = 0.0
            source.progress_detail = "Restarting after an interrupted run"
        await db.commit()
        ids = [str(s.id) for s in stranded]

    for source_id in ids:
        await ctx["redis"].enqueue_job("ingest_source", source_id)
    if ids:
        log.info("ingest.requeued_interrupted", count=len(ids))


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
            source.progress_detail = None
            await db.flush()
            await refresh_workspace_status(db, source.workspace_id)
            await db.commit()
            log.error("ingest.failed", source_id=source_id, error=str(exc))
            raise


async def _run(db, source: Source) -> None:
    source.status = SourceStatus.PARSING
    source.error = None
    await _report(db, source, 0.02, "Reading the file")

    images: dict[str, str] = {}
    if source.type is SourceType.PDF:
        converted = await get_converter().convert(source.file_path)
        text = converted.markdown
        for image_id, data in converted.images.items():
            images[image_id] = str(save_image(source.workspace_id, source.id, image_id, data))
    elif source.type is SourceType.URL:

        async def crawling(fraction: float, detail: str) -> None:
            await _report(db, source, 0.02 + (CONVERTED - 0.02) * fraction, detail)

        pages = await crawl(source.origin, report=crawling)
        text = pages_to_markdown(pages)
        await asyncio.to_thread(Path(source.file_path).write_text, text)
    elif source.type is SourceType.GITHUB:
        await _report(db, source, 0.05, "Cloning repository")
        text = await asyncio.to_thread(repo_to_markdown, source.origin)
        await asyncio.to_thread(Path(source.file_path).write_text, text)
    else:
        text = extract_text(source.file_path, source.type)

    await _report(db, source, CONVERTED, "Splitting into sections")

    parents = chunk_document(text)
    if not parents:
        raise ValueError("No text could be extracted from this file")

    source.status = SourceStatus.EMBEDDING
    await _report(db, source, CHUNKED, f"Preparing {len(parents)} sections")

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
        done = min(start + EMBED_BATCH, len(pending))
        span = EMBEDDED - CHUNKED
        await _report(
            db,
            source,
            CHUNKED + span * done / len(pending),
            f"Embedding {done}/{len(pending)} passages",
        )

    source.status = SourceStatus.READY
    await _report(db, source, 1.0, None)
    await refresh_workspace_status(db, source.workspace_id)
    await db.commit()
    await _refresh_outline(db, source.workspace_id)
    log.info(
        "ingest.done",
        source_id=str(source.id),
        parents=len(parents),
        children=len(pending),
        images=len(images),
    )
