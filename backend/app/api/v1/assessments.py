import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_owned_workspace
from app.core.database import get_db
from app.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentQuestion,
    ReviewItem,
    TopicMastery,
    User,
    Workspace,
)
from app.schemas.assessment import (
    AssessmentCreate,
    AssessmentResponse,
    AssessmentSubmit,
    AssessmentSummary,
    MasteryResponse,
    ReviewItemResponse,
    ReviewResult,
    ReviewSubmit,
)
from app.services.assessment import (
    create_assessment_from_bank,
    grade_response,
    public_question,
)
from app.services.mastery import (
    adjust_plan_for_weak_topics,
    record_mastery,
    topic_from_snapshot,
    update_review_item,
)

router = APIRouter(tags=["assessments"])


def _question_payload(
    item: AssessmentQuestion,
    answer: AssessmentAnswer | None,
    reveal: bool,
) -> dict[str, Any]:
    payload = {
        "id": item.id,
        "position": item.position,
        "points": item.points,
        **public_question(item.question_snapshot, reveal=reveal),
    }
    if answer is not None:
        payload.update(
            response=answer.response,
            score_fraction=answer.score_fraction,
            correct=answer.correct,
            feedback=answer.feedback,
            grader=answer.grader,
            grader_model=answer.grader_model,
        )
    return payload


def _assessment_payload(
    assessment: Assessment, answers: list[AssessmentAnswer] | None = None
) -> dict[str, Any]:
    answer_by_question = {
        answer.assessment_question_id: answer
        for answer in (answers if answers is not None else assessment.answers)
    }
    reveal = assessment.status != "in_progress"
    return {
        "id": assessment.id,
        "title": assessment.title,
        "status": assessment.status,
        "time_limit_minutes": assessment.time_limit_minutes,
        "started_at": assessment.started_at,
        "submitted_at": assessment.submitted_at,
        "score": assessment.score,
        "max_score": assessment.max_score,
        "created_at": assessment.created_at,
        "questions": [
            _question_payload(item, answer_by_question.get(item.id), reveal)
            for item in assessment.questions
        ],
    }


async def _owned_assessment(
    db: AsyncSession,
    assessment_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Assessment:
    query = (
        select(Assessment)
        .options(selectinload(Assessment.questions), selectinload(Assessment.answers))
        .where(
            Assessment.id == assessment_id,
            Assessment.workspace_id == workspace_id,
            Assessment.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found")
    return row


@router.post(
    "/workspaces/{workspace_id}/assessments",
    response_model=AssessmentResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_assessment(
    body: AssessmentCreate,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        assessment = await create_assessment_from_bank(
            db,
            workspace.id,
            user.id,
            title=body.title,
            count=body.count,
            time_limit_minutes=body.time_limit_minutes,
            topic=body.topic,
        )
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{exc}; generate at least {body.count} first",
        ) from exc
    return _assessment_payload(assessment, [])


@router.get("/workspaces/{workspace_id}/assessments", response_model=list[AssessmentSummary])
async def list_assessments(
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Assessment]:
    return list(
        await db.scalars(
            select(Assessment)
            .where(Assessment.workspace_id == workspace.id, Assessment.user_id == user.id)
            .order_by(Assessment.created_at.desc())
        )
    )


@router.get(
    "/workspaces/{workspace_id}/assessments/{assessment_id}",
    response_model=AssessmentResponse,
    response_model_exclude_none=True,
)
async def get_assessment(
    assessment_id: uuid.UUID,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _owned_assessment(db, assessment_id, workspace.id, user.id)
    return _assessment_payload(row)


@router.post(
    "/workspaces/{workspace_id}/assessments/{assessment_id}/submit",
    response_model=AssessmentResponse,
    response_model_exclude_none=True,
)
async def submit_assessment(
    assessment_id: uuid.UUID,
    body: AssessmentSubmit,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    workspace_id_value = workspace.id
    user_id_value = user.id
    assessment = await _owned_assessment(db, assessment_id, workspace_id_value, user_id_value)
    if assessment.status != "in_progress":
        return _assessment_payload(assessment)

    # Copy immutable grading inputs, then release the read transaction before
    # potentially slow LLM grading. A row lock is acquired only for the final
    # atomic write, so concurrent submissions cannot update mastery twice.
    grading_inputs = [
        {
            "id": item.id,
            "question_id": item.question_id,
            "snapshot": item.question_snapshot,
            "points": item.points,
            "response": body.answers.get(str(item.id)),
        }
        for item in assessment.questions
    ]
    await db.rollback()
    grades = await asyncio.gather(
        *(grade_response(item["snapshot"], item["response"]) for item in grading_inputs)
    )

    assessment = await _owned_assessment(
        db, assessment_id, workspace_id_value, user_id_value, for_update=True
    )
    if assessment.status != "in_progress":
        return _assessment_payload(assessment)

    now = datetime.now(UTC)
    expired = now > assessment.started_at + timedelta(minutes=assessment.time_limit_minutes)
    answer_rows = []
    earned = 0.0
    weak_topics: set[str] = set()
    question_by_id = {item.id: item for item in assessment.questions}
    for grading_input, grade in zip(grading_inputs, grades, strict=True):
        item = question_by_id[grading_input["id"]]
        response = grading_input["response"]
        answer = AssessmentAnswer(
            assessment_id=assessment.id,
            assessment_question_id=item.id,
            response=response,
            score_fraction=grade.fraction,
            correct=grade.correct,
            feedback=grade.feedback,
            grader=grade.grader,
            grader_model=grade.model,
        )
        db.add(answer)
        answer_rows.append(answer)
        earned += item.points * grade.fraction
        topic = topic_from_snapshot(item.question_snapshot)
        if not grade.correct:
            weak_topics.add(topic)
        await record_mastery(db, workspace_id_value, user_id_value, topic, grade.fraction)
        await update_review_item(
            db,
            workspace_id_value,
            user_id_value,
            item.question_id,
            item.question_snapshot,
            grade.correct,
            now=now,
        )

    await adjust_plan_for_weak_topics(db, workspace_id_value, user_id_value, weak_topics)

    assessment.status = "timed_out" if expired else "submitted"
    assessment.submitted_at = now
    assessment.score = round(earned, 2)
    await db.flush()
    return _assessment_payload(assessment, answer_rows)


def _review_payload(item: ReviewItem, reveal: bool = False) -> dict[str, Any]:
    return {
        "id": item.id,
        "topic": item.topic,
        "due_at": item.due_at,
        "interval_days": item.interval_days,
        "repetitions": item.repetitions,
        "last_correct": item.last_correct,
        "question": public_question(item.question_snapshot, reveal=reveal),
    }


@router.get("/workspaces/{workspace_id}/reviews", response_model=list[ReviewItemResponse])
async def list_reviews(
    due_only: bool = True,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    query = select(ReviewItem).where(
        ReviewItem.workspace_id == workspace.id,
        ReviewItem.user_id == user.id,
        ReviewItem.active.is_(True),
    )
    if due_only:
        query = query.where(ReviewItem.due_at <= datetime.now(UTC))
    rows = await db.scalars(query.order_by(ReviewItem.due_at))
    return [_review_payload(item) for item in rows]


@router.post("/workspaces/{workspace_id}/reviews/{review_id}/answer", response_model=ReviewResult)
async def answer_review(
    review_id: uuid.UUID,
    body: ReviewSubmit,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    item = await db.get(ReviewItem, review_id)
    if item is None or item.workspace_id != workspace.id or item.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review item not found")
    grade = await grade_response(item.question_snapshot, body.response)
    now = datetime.now(UTC)
    updated = await update_review_item(
        db,
        workspace.id,
        user.id,
        item.question_id,
        item.question_snapshot,
        grade.correct,
        reviewed=True,
        now=now,
    )
    await record_mastery(db, workspace.id, user.id, item.topic, grade.fraction)
    await db.flush()
    return {
        "item": _review_payload(updated or item, reveal=True),
        "score_fraction": grade.fraction,
        "correct": grade.correct,
        "feedback": grade.feedback,
        "grader": grade.grader,
        "grader_model": grade.model,
    }


@router.get("/workspaces/{workspace_id}/mastery", response_model=list[MasteryResponse])
async def list_mastery(
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TopicMastery]:
    rows = await db.scalars(
        select(TopicMastery)
        .where(TopicMastery.workspace_id == workspace.id, TopicMastery.user_id == user.id)
        .order_by(TopicMastery.score, TopicMastery.topic)
    )
    return list(rows)
