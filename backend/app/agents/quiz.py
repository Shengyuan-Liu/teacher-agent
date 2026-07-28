"""Quiz graph: gather -> generate -> validate.

Questions are grounded in sampled sections of the material and carry their
source, so a learner can always jump back to the passage a question came from.
Validation is programmatic: a malformed question is dropped, not repaired.
"""

import json
import random
import re
import uuid
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models import ChunkParent, Source
from app.rag.retriever import RetrievalConfig, retrieve
from app.services import usage
from app.services.providers import chat_model

SAMPLE_SECTIONS = 8
MIN_SECTION_CHARS = 400

GENERATE_PROMPT = """Write practice questions from the numbered excerpts below.

{excerpts}

Produce JSON:
{{"questions": [{{"type": "single|multi|fill|short", "difficulty": "easy|medium|hard",
"stem": "...", "options": ["..."], "answer": ..., "explanation": "...", "source": 1}}]}}

Rules:
- Exactly {count} questions, mixing types; each answerable from its excerpt alone.
- single: 4 options, `answer` is the correct option text.
- multi: 4-5 options, `answer` is a list of the correct option texts (2+).
- fill: `stem` contains ____ for the blank, `answer` is the missing text, no options.
- short: a question needing a 1-3 sentence reply, `answer` is a reference answer.
- `explanation` states why the answer is right, citing the excerpt's reasoning.
- `source` is the number of the excerpt the question is grounded in.
- Mathematics in LaTeX between $ delimiters.
- Write in the same language as the excerpts.
- Output only the JSON object."""


class QuizState(TypedDict):
    workspace_id: str
    count: int
    topic: str | None
    sections: list[dict]
    raw: list[dict]
    questions: list[dict]


async def gather(state: QuizState) -> dict:
    workspace_id = uuid.UUID(state["workspace_id"])
    if state["topic"]:
        hits = await retrieve(workspace_id, state["topic"], RetrievalConfig(top_k=SAMPLE_SECTIONS))
        sections = [
            {
                "chunk_id": h.chunk_id,
                "title": h.source_title,
                "heading": h.heading,
                "content": h.content,
            }
            for h in hits
        ]
    else:
        async with AsyncSessionLocal() as db:
            rows = await db.execute(
                select(ChunkParent, Source.title)
                .join(Source, ChunkParent.source_id == Source.id)
                .where(
                    ChunkParent.workspace_id == workspace_id,
                    func.length(ChunkParent.content) > MIN_SECTION_CHARS,
                )
            )
            parents = list(rows)
        random.shuffle(parents)
        sections = [
            {"chunk_id": str(p.id), "title": title, "heading": p.heading_path, "content": p.content}
            for p, title in parents[:SAMPLE_SECTIONS]
        ]
    if not sections:
        raise ValueError("No material found to write questions from")
    return {"sections": sections}


async def generate(state: QuizState) -> dict:
    excerpts = "\n\n".join(
        f"[{i}] ({s['title']}{' — ' + s['heading'] if s['heading'] else ''})\n"
        + s["content"][:2500]
        for i, s in enumerate(state["sections"], 1)
    )
    prompt = GENERATE_PROMPT.format(excerpts=excerpts, count=state["count"])
    reply = await chat_model().ainvoke([HumanMessage(prompt)])
    usage.record_message("quiz_generate", reply)
    match = re.search(r"\{.*\}", reply.text, re.S)
    if not match:
        raise ValueError("The quiz reply was not valid JSON")
    return {"raw": json.loads(match.group(0)).get("questions", [])}


async def validate(state: QuizState) -> dict:
    questions = []
    for raw in state["raw"]:
        cleaned = validate_question(raw, len(state["sections"]))
        if cleaned is None:
            continue
        section = state["sections"][cleaned.pop("source_index") - 1]
        cleaned["source"] = {
            "chunk_id": section["chunk_id"],
            "title": section["title"],
            "heading": section["heading"],
        }
        questions.append(cleaned)
    if not questions:
        raise ValueError("No generated question survived validation")
    return {"questions": questions}


def validate_question(raw: dict, section_count: int) -> dict | None:
    kind = raw.get("type")
    stem = str(raw.get("stem") or "").strip()
    answer = raw.get("answer")
    options = raw.get("options")
    source = raw.get("source")
    if kind not in ("single", "multi", "fill", "short") or not stem or answer in (None, "", []):
        return None
    if not isinstance(source, int) or not 1 <= source <= section_count:
        return None

    if kind == "single":
        if not isinstance(options, list) or len(options) < 3 or answer not in options:
            return None
    elif kind == "multi":
        if not isinstance(options, list) or len(options) < 3:
            return None
        if not isinstance(answer, list) or len(answer) < 2 or any(a not in options for a in answer):
            return None
    else:
        options = None
        if kind == "fill" and "____" not in stem:
            return None

    return {
        "type": kind,
        "difficulty": raw.get("difficulty")
        if raw.get("difficulty") in ("easy", "medium", "hard")
        else "medium",
        "stem": stem,
        "options": options,
        "answer": answer,
        "explanation": str(raw.get("explanation") or "").strip(),
        "source_index": source,
    }


def build_quiz_graph():
    builder = StateGraph(QuizState)
    builder.add_node("gather", gather)
    builder.add_node("generate", generate)
    builder.add_node("validate", validate)
    builder.add_edge(START, "gather")
    builder.add_edge("gather", "generate")
    builder.add_edge("generate", "validate")
    builder.add_edge("validate", END)
    return builder.compile()


quiz_graph = build_quiz_graph()
