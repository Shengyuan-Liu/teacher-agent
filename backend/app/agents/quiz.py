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
from app.services.mastery import mastery_summary
from app.services.providers import IntelligenceTier, chat_model

SAMPLE_SECTIONS = 8
MIN_SECTION_CHARS = 400

GENERATE_PROMPT = """Write practice questions from the numbered excerpts below.

{excerpts}

The learner asked: {request}

Produce JSON:
{{"questions": [{{"type": "single|multi|fill|short", "difficulty": "easy|medium|hard",
"stem": "...", "options": ["..."], "answer": ..., "explanation": "...", "source": 1}}]}}

Rules:
- Honour the learner's request for how many questions and which type(s). If they
  did not specify, produce {count} questions mixing types. Each must be
  answerable from its excerpt alone.
- single: 4 options, `answer` is the correct option text.
- multi: 4-5 options, `answer` is a list of the correct option texts (2+).
- fill: `stem` contains ____ for the blank, `answer` is the missing text, no options.
- short: a question needing a 1-3 sentence reply, `answer` is a reference answer.
- `explanation` states why the answer is right, citing the excerpt's reasoning.
- `source` is the number of the excerpt the question is grounded in.
- Mathematics in LaTeX between $ delimiters.
- {language}
- Output only the JSON object."""

GROUNDING_PROMPT = """Check whether each proposed practice question and its stated answer are
fully supported by the associated source excerpt. Reject questions that require outside
knowledge, have an ambiguous or partly wrong answer, or whose explanation contradicts the source.

{items}

Return only JSON: {{"supported": [1, 3]}}
The array contains the numbers of supported questions. Be strict."""


class QuizState(TypedDict):
    workspace_id: str
    user_id: str | None
    count: int
    topic: str | None
    #: the learner's natural-language ask and the language to write in; set when
    #: quiz is reached through chat, empty for the legacy quiz endpoint
    request: str
    language: str
    sections: list[dict]
    raw: list[dict]
    questions: list[dict]


async def gather(state: QuizState) -> dict:
    workspace_id = uuid.UUID(state["workspace_id"])
    focus = state["topic"]
    if not focus and state.get("user_id"):
        async with AsyncSessionLocal() as db:
            weakest = await mastery_summary(db, workspace_id, uuid.UUID(state["user_id"]), limit=1)
        if weakest:
            focus = weakest[0].topic
    if focus:
        hits = await retrieve(workspace_id, focus, RetrievalConfig(top_k=SAMPLE_SECTIONS))
        sections = [
            {
                "chunk_id": h.chunk_id,
                "title": h.source_title,
                "heading": h.heading,
                "content": h.content,
                "source_id": h.source_id,
                "source_type": h.source_type,
                "source_origin": h.source_origin,
                "source_url": h.source_url,
                "source_position": h.source_position,
                "page_start": h.page_start,
                "page_end": h.page_end,
            }
            for h in hits
        ]
    else:
        async with AsyncSessionLocal() as db:
            rows = await db.execute(
                select(ChunkParent, Source)
                .join(Source, ChunkParent.source_id == Source.id)
                .where(
                    ChunkParent.workspace_id == workspace_id,
                    func.length(ChunkParent.content) > MIN_SECTION_CHARS,
                )
            )
            parents = list(rows)
        random.shuffle(parents)
        sections = [
            {
                "chunk_id": str(p.id),
                "title": source.title,
                "heading": p.heading_path,
                "content": p.content,
                "source_id": str(source.id),
                "source_type": source.type.value,
                "source_origin": source.origin,
                "source_url": source.origin,
                "source_position": p.position,
            }
            for p, source in parents[:SAMPLE_SECTIONS]
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
    prompt = GENERATE_PROMPT.format(
        excerpts=excerpts,
        count=state["count"],
        request=state.get("request") or "(no specific request — use your judgement)",
        language=state.get("language")
        or "Write the questions in the same language as the excerpts.",
    )
    reply = await chat_model(IntelligenceTier.SMART).ainvoke([HumanMessage(prompt)])
    usage.record_message("quiz_generate", reply)
    match = re.search(r"\{.*\}", reply.text, re.S)
    if not match:
        raise ValueError("The quiz reply was not valid JSON")
    return {"raw": json.loads(match.group(0)).get("questions", [])}


async def validate(state: QuizState) -> dict:
    candidates = _clean_candidates(state["raw"], state["sections"])
    if not candidates:
        raise ValueError("No generated question survived validation")

    supported = await judge_grounding(candidates, state["sections"])
    questions = [question for index, question in enumerate(candidates, 1) if index in supported]

    # One bounded repair pass keeps a rejected item from silently reducing the
    # requested quiz size. The repaired set goes through the same strict judge.
    if len(questions) < state["count"]:
        missing = state["count"] - len(questions)
        avoid = "; ".join(question["stem"] for question in questions)
        retry_state = {
            **state,
            "count": missing,
            "request": (
                f"{state.get('request') or ''}\nGenerate {missing} replacement questions. "
                f"Do not repeat these stems: {avoid or '(none)'}"
            ),
        }
        replacement_raw = (await generate(retry_state))["raw"]
        replacements = _clean_candidates(replacement_raw, state["sections"])
        replacements = deduplicate_questions([*questions, *replacements])[len(questions) :]
        if replacements:
            replacement_supported = await judge_grounding(replacements, state["sections"])
            questions.extend(
                question
                for index, question in enumerate(replacements, 1)
                if index in replacement_supported
            )

    if not questions:
        raise ValueError("No generated question was fully supported by the material")
    return {"questions": questions[: state["count"]]}


def _clean_candidates(raw_questions: list[dict], sections: list[dict]) -> list[dict]:
    candidates = []
    for raw in raw_questions:
        cleaned = validate_question(raw, len(sections))
        if cleaned is None:
            continue
        index = cleaned.pop("source_index")
        section = sections[index - 1]
        cleaned["source"] = {
            "index": index,
            "chunk_id": section["chunk_id"],
            "title": section["title"],
            "heading": section["heading"],
            "source_id": section.get("source_id"),
            "source_type": section.get("source_type"),
            "source_origin": section.get("source_origin"),
            "source_url": section.get("source_url"),
            "source_position": section.get("source_position"),
            "page_start": section.get("page_start"),
            "page_end": section.get("page_end"),
        }
        candidates.append(cleaned)
    return deduplicate_questions(candidates)


async def judge_grounding(questions: list[dict], sections: list[dict]) -> set[int]:
    items = []
    for index, question in enumerate(questions, 1):
        source_index = int(question["source"]["index"])
        excerpt = sections[source_index - 1]["content"][:3000]
        items.append(
            f"Question {index}: {question['stem']}\n"
            f"Stated answer: {json.dumps(question['answer'], ensure_ascii=False)}\n"
            f"Explanation: {question['explanation']}\n"
            f"Source excerpt:\n{excerpt}"
        )
    reply = await chat_model(IntelligenceTier.SMART).ainvoke(
        [HumanMessage(GROUNDING_PROMPT.format(items="\n\n---\n\n".join(items)))]
    )
    usage.record_message("quiz_validate", reply)
    return parse_supported(reply.text, len(questions))


def parse_supported(text: str, question_count: int) -> set[int]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return set()
    try:
        values = json.loads(match.group(0)).get("supported", [])
    except (json.JSONDecodeError, AttributeError):
        return set()
    return {
        value
        for value in values
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= question_count
    }


def deduplicate_questions(questions: list[dict]) -> list[dict]:
    unique = []
    seen: set[str] = set()
    for question in questions:
        key = re.sub(r"[^\w]+", "", question["stem"].casefold())
        if key and key not in seen:
            unique.append(question)
            seen.add(key)
    return unique


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
