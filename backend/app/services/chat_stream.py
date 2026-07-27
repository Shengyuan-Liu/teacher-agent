import json
import re
import uuid
from collections.abc import AsyncGenerator

import structlog
from sqlalchemy import select

from app.agents.qa import qa_graph
from app.core.database import AsyncSessionLocal
from app.models import ChatSession, Message
from app.rag.retriever import RetrievedChunk

log = structlog.get_logger()

HISTORY_TURNS = 6


EXCERPT_CHARS = 600
CITED = re.compile(r"\[(\d+)\]")


def _cited_numbers(answer: str) -> set[int]:
    return {int(n) for n in CITED.findall(answer)}


def _citations_payload(context: list[RetrievedChunk]) -> list[dict]:
    return [
        {
            "n": i,
            "chunk_id": c.chunk_id,
            "source_id": c.source_id,
            "source_title": c.source_title,
            "heading": c.heading,
            "excerpt": c.content[:EXCERPT_CHARS],
            "truncated": len(c.content) > EXCERPT_CHARS,
            "images": [i["id"] for i in c.images],
        }
        for i, c in enumerate(context, 1)
    ]


async def stream_answer(session_id: uuid.UUID, question: str) -> AsyncGenerator[dict, None]:
    """Run the QA graph and yield SSE events: citations, token, done."""
    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        history = await db.scalars(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(HISTORY_TURNS)
        )
        history_pairs = [(m.role, m.content) for m in reversed(list(history))]

        db.add(Message(session_id=session_id, role="user", content=question))
        if session.title is None:
            session.title = question[:60]
        await db.commit()

    state = {
        "question": question,
        "history": history_pairs,
        "workspace_id": str(session.workspace_id),
        "context": [],
        "grounded": False,
        "answer": "",
    }

    citations: list[dict] = []
    answer_parts: list[str] = []
    grounded = False

    # Stage events keep the client informed while nodes run, so the UI can
    # render a live call chain instead of a blank wait.
    yield {"event": "stage", "data": json.dumps({"stage": "retrieve"})}

    try:
        async for mode, payload in qa_graph.astream(state, stream_mode=["updates", "messages"]):
            if mode == "messages":
                chunk, metadata = payload
                if metadata.get("langgraph_node") in ("generate", "decline") and chunk.text:
                    answer_parts.append(chunk.text)
                    yield {"event": "token", "data": json.dumps({"delta": chunk.text})}
            elif mode == "updates":
                if "retrieve" in payload:
                    citations = _citations_payload(payload["retrieve"]["context"])
                    yield {
                        "event": "stage",
                        "data": json.dumps({"stage": "grade", "excerpts": len(citations)}),
                    }
                if "grade" in payload:
                    grounded = payload["grade"]["grounded"]
                    yield {
                        "event": "stage",
                        "data": json.dumps({"stage": "generate" if grounded else "decline"}),
                    }
                    if grounded:
                        yield {"event": "citations", "data": json.dumps(citations)}
    except Exception as exc:
        log.error("chat.stream_failed", session_id=str(session_id), error=str(exc))
        yield {"event": "error", "data": json.dumps({"message": str(exc)[:500]})}
        return

    answer = "".join(answer_parts)
    # Retrieval returns top-k; only what the answer actually referenced belongs
    # in the citation list, or the reader is handed unrelated sources.
    used = _cited_numbers(answer)
    if used and grounded:
        citations = [c for c in citations if c["n"] in used]
        # The earlier event carried every candidate; replace it now that the
        # answer has shown which ones it leaned on.
        yield {"event": "citations", "data": json.dumps(citations)}

    async with AsyncSessionLocal() as db:
        message = Message(
            session_id=session_id,
            role="assistant",
            content=answer,
            citations=citations if grounded else None,
        )
        db.add(message)
        await db.commit()
        yield {
            "event": "done",
            "data": json.dumps({"message_id": str(message.id), "grounded": grounded}),
        }
