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
from app.services.providers import chat_model

log = structlog.get_logger()

MAX_HEADING_LINES = 250

PROMPT = """You are mapping a body of study material into a topic outline.

The material consists of these sources and their section headings:

{headings}

Produce a JSON object:
{{"topics": [{{"id": "t1", "title": "...", "summary": "...", "depends_on": ["t0"]}}]}}

Rules:
- 6 to 15 topics that together cover the material; merge tiny sections.
- Order topics so prerequisites come first; `depends_on` lists topic ids that
  must be understood before this one.
- `summary` is one sentence on what the topic covers, grounded in the headings.
- Use the same language the material is written in.
- Output only the JSON object."""


async def corpus_headings(workspace_id: uuid.UUID) -> str:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(Source.title, ChunkParent.heading_path)
            .join(ChunkParent, ChunkParent.source_id == Source.id)
            .where(ChunkParent.workspace_id == workspace_id)
            .order_by(Source.title, ChunkParent.position)
        )
        by_source: dict[str, list[str]] = {}
        for source_title, heading in rows:
            seen = by_source.setdefault(source_title, [])
            if heading and heading not in seen:
                seen.append(heading)

    lines: list[str] = []
    for source_title, headings in by_source.items():
        lines.append(f"## {source_title}")
        lines.extend(f"- {h}" for h in headings)
    return "\n".join(lines[:MAX_HEADING_LINES])


async def build_outline(workspace_id: uuid.UUID) -> dict:
    headings = await corpus_headings(workspace_id)
    if not headings:
        raise ValueError("The workspace has no ingested material yet")

    reply = await chat_model().ainvoke([HumanMessage(PROMPT.format(headings=headings))])
    usage.record_message("outline", reply)
    parsed = _parse(reply.text)
    if parsed is None:
        raise ValueError("The outline reply was not valid JSON")

    outline = {"generated_at": datetime.now(UTC).isoformat(), "topics": parsed["topics"]}
    async with AsyncSessionLocal() as db:
        workspace = await db.get(Workspace, workspace_id)
        workspace.outline_json = outline
        await db.commit()
    log.info("outline.built", workspace_id=str(workspace_id), topics=len(parsed["topics"]))
    return outline


async def ensure_outline(workspace_id: uuid.UUID) -> dict:
    async with AsyncSessionLocal() as db:
        workspace = await db.get(Workspace, workspace_id)
        if workspace.outline_json:
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
    topics = parsed.get("topics")
    if not isinstance(topics, list) or not topics:
        return None
    return {"topics": [t for t in topics if t.get("id") and t.get("title")]}
