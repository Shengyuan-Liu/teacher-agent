"""Attaching retrieved figures to the answer prompt.

Lecture notes carry meaning in their diagrams — a unit ball, a separating
hyperplane — that the surrounding prose does not restate. When a retrieved
section references figures we send the images alongside the text, capped so a
figure-heavy section cannot blow up the request.
"""

import base64
import mimetypes
from pathlib import Path

import structlog

from app.core.config import settings
from app.rag.retriever import RetrievedChunk

log = structlog.get_logger()


def image_blocks(context: list[RetrievedChunk]) -> list[dict]:
    """Content blocks for the figures in `context`, best-ranked first."""
    if not settings.answer_with_images:
        return []

    blocks: list[dict] = []
    for chunk in context:
        for image in chunk.images:
            if len(blocks) >= settings.max_answer_images:
                return blocks
            block = _block(Path(image["path"]), image["id"])
            if block:
                blocks.append(block)
    return blocks


def _block(path: Path, image_id: str) -> dict | None:
    if not path.is_file():
        log.warning("vision.image_missing", path=str(path))
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return {
        "type": "image",
        "source_type": "base64",
        "mime_type": mime,
        "data": base64.b64encode(path.read_bytes()).decode(),
        "metadata": {"figure": image_id},
    }
