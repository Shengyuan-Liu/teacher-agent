"""Golden set for retrieval and answer evaluation.

Questions are generated from sampled parent chunks, so the chunk they came from
is the ground truth for Recall@k, and its text is the reference for
correctness. Out-of-scope questions carry no gold chunk and exist to check that
the pipeline declines instead of inventing an answer.
"""

import json
import random
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from langchain_core.messages import HumanMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChunkParent
from app.services.providers import chat_model

GENERATE_PROMPT = """Here is an excerpt from a set of lecture notes.

---
{content}
---

Write one question that this excerpt answers, and the answer, as JSON:
{{"question": "...", "answer": "..."}}

The question must be answerable from this excerpt alone. Ask it the way a
student would, in their own words: paraphrase rather than reusing the
excerpt's distinctive phrasing, and do not quote its sentences. Keep any
symbol or named result that a student would genuinely say out loud. Do not
refer to "the excerpt" or "the passage". Keep the answer under 60 words."""

OUT_OF_SCOPE = [
    "Who won the 2022 FIFA World Cup?",
    "What is the capital of Australia?",
    "How do I make sourdough bread?",
]


@dataclass
class EvalCase:
    question: str
    gold_parent_id: str | None
    reference_answer: str | None
    source_title: str | None


def load(path: Path) -> list[EvalCase]:
    payload = json.loads(path.read_text())
    return [EvalCase(**row) for row in payload]


def save(cases: list[EvalCase], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(c) for c in cases], indent=2, ensure_ascii=False))


async def build(
    db: AsyncSession, workspace_id: uuid.UUID, size: int, seed: int = 7
) -> list[EvalCase]:
    rows = await db.execute(
        select(ChunkParent)
        .where(ChunkParent.workspace_id == workspace_id, func.length(ChunkParent.content) > 800)
        .order_by(ChunkParent.id)
    )
    parents = list(rows.scalars())
    random.Random(seed).shuffle(parents)

    cases: list[EvalCase] = []
    for parent in parents[:size]:
        reply = await chat_model().ainvoke(
            [HumanMessage(GENERATE_PROMPT.format(content=parent.content[:3000]))]
        )
        parsed = _parse(reply.text)
        if parsed is None:
            continue
        cases.append(
            EvalCase(
                question=parsed["question"],
                gold_parent_id=str(parent.id),
                reference_answer=parsed["answer"],
                source_title=parent.heading_path,
            )
        )

    cases.extend(
        EvalCase(question=q, gold_parent_id=None, reference_answer=None, source_title=None)
        for q in OUT_OF_SCOPE
    )
    return cases


def _parse(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not parsed.get("question") or not parsed.get("answer"):
        return None
    return parsed
