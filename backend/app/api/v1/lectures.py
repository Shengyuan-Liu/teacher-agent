import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_workspace
from app.core.database import get_db
from app.models import LectureSession, User, Workspace
from app.schemas.lecture import LectureDetail, LectureSummary

router = APIRouter(tags=["lectures"])


def _summary(row: LectureSession) -> dict:
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "chat_session_id": row.chat_session_id,
        "plan_stage_id": row.plan_stage_id,
        "title": row.title,
        "scope": row.scope,
        "status": row.status,
        "current_section_index": row.current_section_index,
        "total_sections": len(row.outline),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "completed_at": row.completed_at,
    }


def _detail(row: LectureSession) -> dict:
    pending = row.pending_check
    # The reference answer remains server-only until the learner responds.
    visible_pending = (
        {
            key: value
            for key, value in pending.items()
            if key not in ("expected_answer", "explanation")
        }
        if pending
        else None
    )
    return {
        **_summary(row),
        "outline": row.outline,
        "pending_check": visible_pending,
        "section_history": row.section_history,
    }


async def _owned_lecture(
    lecture_id: uuid.UUID,
    workspace: Workspace,
    user: User,
    db: AsyncSession,
) -> LectureSession:
    row = await db.get(LectureSession, lecture_id)
    if row is None or row.workspace_id != workspace.id or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lecture not found")
    return row


@router.get(
    "/workspaces/{workspace_id}/lectures",
    response_model=list[LectureSummary],
)
async def list_lectures(
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = await db.scalars(
        select(LectureSession)
        .where(
            LectureSession.workspace_id == workspace.id,
            LectureSession.user_id == user.id,
        )
        .order_by(LectureSession.updated_at.desc())
    )
    return [_summary(row) for row in rows]


@router.get(
    "/workspaces/{workspace_id}/lectures/{lecture_id}",
    response_model=LectureDetail,
)
async def get_lecture(
    lecture_id: uuid.UUID,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return _detail(await _owned_lecture(lecture_id, workspace, user, db))
