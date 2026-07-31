"""User-facing governance for long-term Agent memory."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_workspace
from app.core.database import get_db
from app.models import User, UserMemory, Workspace
from app.schemas.memory import MemoryCreate, MemoryResponse, MemoryUpdate
from app.services.memory import effective_confidence, manual_memory_key
from app.services.providers import embeddings

router = APIRouter(tags=["memory"])


def _payload(memory: UserMemory) -> dict:
    return {
        "id": memory.id,
        "kind": memory.kind,
        "memory_key": memory.memory_key,
        "content": memory.content,
        "confidence": memory.confidence,
        "effective_confidence": effective_confidence(memory),
        "importance": memory.importance,
        "user_confirmed": memory.user_confirmed,
        "expires_at": memory.expires_at,
        "last_accessed_at": memory.last_accessed_at,
        "access_count": memory.access_count,
        "source_workspace_id": memory.source_workspace_id,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }


async def _owned_memory(memory_id: uuid.UUID, user: User, db: AsyncSession) -> UserMemory:
    memory = await db.scalar(
        select(UserMemory).where(UserMemory.id == memory_id, UserMemory.user_id == user.id)
    )
    if memory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
    return memory


@router.get(
    "/workspaces/{workspace_id}/memories",
    response_model=list[MemoryResponse],
)
async def list_memories(
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    kind: str | None = Query(default=None, pattern="^(preference|background|goal)$"),
    q: str | None = Query(default=None, max_length=200),
) -> list[dict]:
    del workspace  # ownership is the authorization check; memories are user-global
    query = select(UserMemory).where(
        UserMemory.user_id == user.id,
        or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > datetime.now(UTC)),
    )
    if kind:
        query = query.where(UserMemory.kind == kind)
    if q:
        query = query.where(UserMemory.content.ilike(f"%{q.strip()}%"))
    rows = list(await db.scalars(query.order_by(UserMemory.updated_at.desc()).limit(200)))
    return [_payload(row) for row in rows]


@router.post(
    "/workspaces/{workspace_id}/memories",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_memory(
    body: MemoryCreate,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    key = manual_memory_key(body.kind, body.content)
    memory = UserMemory(
        user_id=user.id,
        source_workspace_id=workspace.id,
        source_session_id=None,
        source_message_id=None,
        kind=body.kind,
        memory_key=key,
        content=body.content,
        confidence=1,
        importance=1,
        user_confirmed=True,
        expires_at=body.expires_at,
        last_accessed_at=None,
        access_count=0,
        embedding=await embeddings().aembed_query(body.content),
    )
    db.add(memory)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A memory with this key already exists"
        ) from None
    return _payload(memory)


@router.patch("/memories/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: uuid.UUID,
    body: MemoryUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    memory = await _owned_memory(memory_id, user, db)
    if body.kind is not None:
        memory.kind = body.kind
    if body.content is not None and body.content != memory.content:
        memory.content = body.content
        memory.embedding = await embeddings().aembed_query(body.content)
    if "expires_at" in body.model_fields_set:
        memory.expires_at = body.expires_at
    memory.confidence = 1
    memory.importance = max(memory.importance, 0.9)
    memory.user_confirmed = True
    await db.flush()
    await db.refresh(memory)
    return _payload(memory)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await db.delete(await _owned_memory(memory_id, user, db))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
