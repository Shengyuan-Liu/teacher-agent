import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_current_user, get_owned_workspace
from app.core.database import get_db
from app.models import Question, StudyPlan, User, Workspace
from app.schemas.plan import (
    PlanCreate,
    QuestionResponse,
    QuizCreate,
    StageUpdate,
    StudyPlanResponse,
)
from app.services.agent_runs import stream_plan, stream_quiz

router = APIRouter(tags=["plans"])


@router.post("/workspaces/{workspace_id}/plans/stream")
async def generate_plan(
    body: PlanCreate,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
) -> EventSourceResponse:
    return EventSourceResponse(
        stream_plan(workspace.id, user.id, body.goal, body.daily_minutes, body.deadline)
    )


@router.get("/workspaces/{workspace_id}/plans", response_model=list[StudyPlanResponse])
async def list_plans(
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[StudyPlan]:
    result = await db.scalars(
        select(StudyPlan)
        .options(selectinload(StudyPlan.stages))
        .where(StudyPlan.workspace_id == workspace.id, StudyPlan.user_id == user.id)
        .order_by(StudyPlan.created_at.desc())
    )
    return list(result)


@router.patch("/plans/{plan_id}/stages/{stage_id}", response_model=StudyPlanResponse)
async def update_stage(
    plan_id: uuid.UUID,
    stage_id: uuid.UUID,
    body: StageUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StudyPlan:
    plan = await db.scalar(
        select(StudyPlan)
        .options(selectinload(StudyPlan.stages))
        .where(StudyPlan.id == plan_id, StudyPlan.user_id == user.id)
    )
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    stage = next((s for s in plan.stages if s.id == stage_id), None)
    if stage is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stage not found")
    stage.status = body.status
    return plan


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    plan = await db.scalar(
        select(StudyPlan).where(StudyPlan.id == plan_id, StudyPlan.user_id == user.id)
    )
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    await db.delete(plan)


@router.post("/workspaces/{workspace_id}/quiz/stream")
async def generate_quiz(
    body: QuizCreate,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
) -> EventSourceResponse:
    return EventSourceResponse(stream_quiz(workspace.id, user.id, body.count, body.topic))


@router.get("/workspaces/{workspace_id}/questions", response_model=list[QuestionResponse])
async def list_questions(
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> list[Question]:
    result = await db.scalars(
        select(Question)
        .where(Question.workspace_id == workspace.id)
        .order_by(Question.created_at.desc())
    )
    return list(result)


@router.delete("/workspaces/{workspace_id}/questions/{question_id}", status_code=204)
async def delete_question(
    question_id: uuid.UUID,
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> None:
    question = await db.get(Question, question_id)
    if question is None or question.workspace_id != workspace.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    await db.delete(question)
