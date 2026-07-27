from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_workspace
from app.core.database import get_db
from app.models import User, Workspace
from app.schemas.workspace import WorkspaceCreate, WorkspaceResponse, WorkspaceUpdate
from app.services.storage import delete_workspace_files

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[Workspace]:
    result = await db.scalars(
        select(Workspace).where(Workspace.owner_id == user.id).order_by(Workspace.created_at.desc())
    )
    return list(result)


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Workspace:
    workspace = Workspace(owner_id=user.id, **body.model_dump())
    db.add(workspace)
    await db.flush()
    return workspace


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(workspace: Workspace = Depends(get_owned_workspace)) -> Workspace:
    return workspace


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    body: WorkspaceUpdate,
    workspace: Workspace = Depends(get_owned_workspace),
) -> Workspace:
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(workspace, field, value)
    return workspace


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> None:
    await db.delete(workspace)
    delete_workspace_files(workspace.id)
