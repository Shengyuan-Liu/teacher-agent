"""Workspace outline: the topic map the planner and quizzes are built on.

A single structured LLM call over the corpus's heading paths, not a graph.
It runs after ingestion settles and can be regenerated on demand; the result
lives on `workspaces.outline_json`.
"""

import json
import re
import uuid
from datetime import UTC, datetime

import structlog
from langchain_core.messages import HumanMessage
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import ChunkParent, Source, Workspace
from app.services import usage
from app.services.providers import IntelligenceTier, chat_model

log = structlog.get_logger()

OUTLINE_VERSION = 2
MAX_STRUCTURE_CHARS = 40_000
LEAD_CHUNKS = 3
LEAD_CHARS = 5_000
EXCERPT_CHARS = 320

PROMPT = """You are mapping a body of study material into a topic outline.

The material below is in source order. It contains section headings when the
source format preserved them; otherwise it contains the document's opening
pages followed by ordered excerpts. A table of contents in the opening pages is
the most authoritative description of the course sequence.

{headings}

Produce a JSON object:
{{"topics": [{{"id": "t1", "title": "...", "summary": "...", "depends_on": ["t0"]}}]}}

Rules:
- Produce 6 to 15 topics that together cover the major chapters actually
  present in the material; merge small adjacent sections.
- Preserve the material's chapter/course order. Do not replace it with a generic
  textbook syllabus and do not invent topics merely because they are common in
  the subject.
- Include every distinct major chapter shown in a table of contents. A later
  chapter must not be moved before an earlier chapter unless the material itself
  explicitly presents it as a prerequisite.
- `depends_on` may contain only ids of earlier topics in this output.
- `summary` is one sentence on what the topic covers, grounded in the headings.
- Use the same language the material is written in.
- Output only the JSON object."""


async def corpus_headings(workspace_id: uuid.UUID) -> str:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(
                Source.title,
                ChunkParent.position,
                ChunkParent.heading_path,
                ChunkParent.content,
            )
            .join(ChunkParent, ChunkParent.source_id == Source.id)
            .where(ChunkParent.workspace_id == workspace_id)
            .order_by(Source.created_at, ChunkParent.position)
        )
        material = list(rows)
    return _render_structure(material)


def _render_structure(rows: list[tuple[str, int, str | None, str]]) -> str:
    """Render reliable ordered context even when plain-text PDF extraction
    discarded all Markdown headings.

    The old implementation returned only the source title in that case. The
    outline model then had no evidence and produced a plausible-but-unrelated
    generic syllabus. Opening chunks are kept in full because PDFs commonly put
    their complete table of contents there; later chunks provide ordered checks
    that those chapters really occur in the body.
    """
    by_source: dict[str, list[tuple[int, str | None, str]]] = {}
    for source_title, position, heading, content in rows:
        by_source.setdefault(source_title, []).append((position, heading, content))

    blocks: list[str] = []
    for source_title, chunks in by_source.items():
        blocks.append(f"## {source_title}")
        headings = [heading.strip() for _, heading, _ in chunks if heading and heading.strip()]
        if headings:
            blocks.append("Ordered headings:")
            blocks.extend(f"- {heading}" for heading in dict.fromkeys(headings))
            continue

        blocks.append("Ordered document excerpts (headings were not preserved):")
        for index, (position, _, content) in enumerate(chunks):
            limit = LEAD_CHARS if index < LEAD_CHUNKS else EXCERPT_CHARS
            excerpt = content.strip()[:limit]
            if excerpt:
                blocks.append(f"[document position {position}]\n{excerpt}")

    return "\n\n".join(blocks)[:MAX_STRUCTURE_CHARS]


async def build_outline(workspace_id: uuid.UUID) -> dict:
    headings = await corpus_headings(workspace_id)
    if not headings:
        raise ValueError("The workspace has no ingested material yet")

    reply = await chat_model(IntelligenceTier.SMART).ainvoke(
        [HumanMessage(PROMPT.format(headings=headings))]
    )
    usage.record_message("outline", reply)
    parsed = _parse(reply.text)
    if parsed is None:
        raise ValueError("The outline reply was not valid JSON")

    outline = {
        "version": OUTLINE_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "topics": parsed["topics"],
    }
    async with AsyncSessionLocal() as db:
        workspace = await db.get(Workspace, workspace_id)
        workspace.outline_json = outline
        await db.commit()
    log.info("outline.built", workspace_id=str(workspace_id), topics=len(parsed["topics"]))
    return outline


async def ensure_outline(workspace_id: uuid.UUID) -> dict:
    async with AsyncSessionLocal() as db:
        workspace = await db.get(Workspace, workspace_id)
        if workspace.outline_json and workspace.outline_json.get("version") == OUTLINE_VERSION:
            return workspace.outline_json
    return await build_outline(workspace_id)


def _parse(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    raw_topics = parsed.get("topics")
    if not isinstance(raw_topics, list) or not raw_topics:
        return None
    raw_topics = [t for t in raw_topics if t.get("id") and t.get("title")]
    if not raw_topics:
        return None

    # Canonical ids make dependencies deterministic. More importantly, future
    # ids are discarded: a plan may follow this list without being sent into a
    # dependency cycle invented by the model.
    id_map = {str(topic["id"]): f"t{i + 1}" for i, topic in enumerate(raw_topics)}
    topics = []
    for index, topic in enumerate(raw_topics):
        earlier_ids = {str(item["id"]) for item in raw_topics[:index]}
        dependencies = [
            id_map[str(dependency)]
            for dependency in topic.get("depends_on", [])
            if str(dependency) in earlier_ids
        ]
        topics.append(
            {
                "id": f"t{index + 1}",
                "title": str(topic["title"]),
                "summary": str(topic.get("summary") or ""),
                "depends_on": list(dict.fromkeys(dependencies)),
            }
        )
    return {"topics": topics}
