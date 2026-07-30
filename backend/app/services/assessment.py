"""Question snapshots and objective/LLM grading for formal assessments."""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Assessment, AssessmentQuestion, Question
from app.services import usage
from app.services.mastery import mastery_summary, topic_from_snapshot
from app.services.providers import IntelligenceTier, chat_model, model_name

SHORT_GRADE_PROMPT = """Grade a learner's short answer using only the reference below.

Question: {stem}
Reference answer: {answer}
Reference explanation: {explanation}
Learner answer: {response}

Return only JSON: {{"score": 0.0, "feedback": "..."}}
`score` is between 0 and 1. Award partial credit for correct reasoning. Do not
require wording identical to the reference and do not use outside knowledge."""


@dataclass
class Grade:
    fraction: float
    correct: bool
    feedback: str
    grader: str
    model: str | None = None


def snapshot_question(question: Question) -> dict[str, Any]:
    return {
        "type": question.type,
        "difficulty": question.difficulty,
        "stem": question.stem,
        "options": question.options,
        "answer": question.answer,
        "explanation": question.explanation,
        "source": question.source,
    }


def public_question(snapshot: dict[str, Any], reveal: bool = False) -> dict[str, Any]:
    payload = {
        key: snapshot.get(key) for key in ("type", "difficulty", "stem", "options", "source")
    }
    if reveal:
        payload.update(answer=snapshot.get("answer"), explanation=snapshot.get("explanation"))
    return payload


async def create_assessment_from_bank(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    title: str,
    count: int,
    time_limit_minutes: int,
    topic: str | None = None,
) -> Assessment:
    """Create a stable assessment snapshot from the question bank.

    Weak topics are selected first, so both the REST compatibility endpoint and
    the chat test agent use the same adaptive ordering.
    """
    bank = list(
        await db.scalars(
            select(Question)
            .where(Question.workspace_id == workspace_id)
            .order_by(Question.created_at.desc())
        )
    )
    if topic:
        needle = topic.casefold()
        bank = [
            question
            for question in bank
            if needle in topic_from_snapshot(snapshot_question(question)).casefold()
            or needle in question.stem.casefold()
        ]
    if len(bank) < count:
        raise ValueError(f"Only {len(bank)} matching questions are available")

    weak = await mastery_summary(db, workspace_id, user_id, limit=100)
    rank = {row.topic.casefold(): row.score for row in weak}
    bank.sort(
        key=lambda question: rank.get(
            topic_from_snapshot(snapshot_question(question)).casefold(), 50.0
        )
    )
    selected = bank[:count]
    assessment = Assessment(
        workspace_id=workspace_id,
        user_id=user_id,
        title=title,
        status="in_progress",
        time_limit_minutes=time_limit_minutes,
        started_at=datetime.now(UTC),
        max_score=float(len(selected)),
        questions=[],
        answers=[],
    )
    db.add(assessment)
    await db.flush()
    for position, question in enumerate(selected):
        item = AssessmentQuestion(
            assessment_id=assessment.id,
            question_id=question.id,
            position=position,
            points=1.0,
            question_snapshot=snapshot_question(question),
        )
        db.add(item)
        assessment.questions.append(item)
    await db.flush()
    return assessment


def _normalise(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def grade_objective(snapshot: dict[str, Any], response: Any) -> Grade:
    kind = snapshot["type"]
    expected = snapshot.get("answer")
    if kind == "multi":
        actual_set = (
            {_normalise(value) for value in response} if isinstance(response, list) else set()
        )
        expected_set = (
            {_normalise(value) for value in expected} if isinstance(expected, list) else set()
        )
        fraction = 1.0 if actual_set == expected_set and bool(expected_set) else 0.0
    else:
        fraction = float(
            bool(_normalise(expected)) and _normalise(response) == _normalise(expected)
        )
    correct = fraction >= 0.7
    feedback = "Correct." if correct else str(snapshot.get("explanation") or "Review the source.")
    return Grade(fraction=fraction, correct=correct, feedback=feedback, grader="automatic")


async def grade_response(snapshot: dict[str, Any], response: Any) -> Grade:
    if snapshot["type"] != "short" or not _normalise(response):
        return grade_objective(snapshot, response)
    reply = await chat_model(IntelligenceTier.SMART).ainvoke(
        [
            HumanMessage(
                SHORT_GRADE_PROMPT.format(
                    stem=snapshot["stem"],
                    answer=snapshot["answer"],
                    explanation=snapshot.get("explanation") or "",
                    response=response,
                )
            )
        ]
    )
    usage.record_message("short_answer_grade", reply)
    parsed = parse_short_grade(reply.text)
    return Grade(
        fraction=parsed[0],
        correct=parsed[0] >= 0.7,
        feedback=parsed[1],
        grader="llm",
        model=model_name(IntelligenceTier.SMART),
    )


def parse_short_grade(text: str) -> tuple[float, str]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return 0.0, "The short-answer grader returned an invalid response."
    try:
        payload = json.loads(match.group(0))
        score = max(0.0, min(1.0, float(payload.get("score", 0))))
        feedback = str(payload.get("feedback") or "No feedback provided.")
        return score, feedback
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.0, "The short-answer grader returned an invalid response."
