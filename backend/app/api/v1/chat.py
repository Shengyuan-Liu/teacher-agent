import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_current_user, get_owned_workspace
from app.core.database import get_db
from app.models import ChatSession, Message, User, Workspace
from app.schemas.chat import AskRequest, ChatSessionResponse, MessageResponse
from app.services.chat_stream import stream_answer

router = APIRouter(tags=["chat"])


@router.post(
    "/workspaces/{workspace_id}/chat/sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatSession:
    session = ChatSession(user_id=user.id, workspace_id=workspace.id)
    db.add(session)
    await db.flush()
    return session


@router.get("/workspaces/{workspace_id}/chat/sessions", response_model=list[ChatSessionResponse])
async def list_sessions(
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChatSession]:
    result = await db.scalars(
        select(ChatSession)
        .where(ChatSession.workspace_id == workspace.id, ChatSession.user_id == user.id)
        .order_by(ChatSession.created_at.desc())
    )
    return list(result)


async def _owned_session(session_id: uuid.UUID, user: User, db: AsyncSession) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return session


@router.delete("/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    session = await _owned_session(session_id, user, db)
    await db.delete(session)


@router.get("/chat/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Message]:
    session = await _owned_session(session_id, user, db)
    result = await db.scalars(
        select(Message).where(Message.session_id == session.id).order_by(Message.created_at)
    )
    return list(result)


@router.post("/chat/sessions/{session_id}/stream")
async def ask(
    session_id: uuid.UUID,
    body: AskRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    session = await _owned_session(session_id, user, db)
    # The intent router (inside stream_answer) decides web vs local, gates it on
    # the deployment flag, and rate-limits it. body.web_search is the explicit
    # "search the web" suggestion click, which forces the web branch.
    return EventSourceResponse(stream_answer(session.id, body.message, body.web_search, user.id))
