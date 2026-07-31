"""Workspace-scoped Prompt Registry with immutable versions."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_owned_workspace
from app.core.database import get_db
from app.models import PromptDefinition, PromptVersion, User, Workspace
from app.prompts.registry import (
    PromptRegistryError,
    clear_prompt_cache,
    get_builtin_prompt,
    list_builtin_prompts,
    prompt_hash,
    template_variables,
)
from app.schemas.prompts import PromptDefinitionResponse, PromptVersionCreate

router = APIRouter(tags=["prompts"])


def _builtin_payload(item, *, active: bool) -> dict:
    return {
        "id": None,
        "version": item.version,
        "status": "active" if active else "builtin",
        "template": item.template,
        "variables": list(item.variables),
        "content_hash": item.content_hash,
        "source": "builtin",
        "notes": "Code-owned fallback",
        "metadata": {},
        "created_at": None,
        "activated_at": None,
    }


def _version_payload(row: PromptVersion) -> dict:
    return {
        "id": row.id,
        "version": row.version,
        "status": row.status,
        "template": row.template,
        "variables": row.variables,
        "content_hash": row.content_hash,
        "source": "workspace",
        "notes": row.notes,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
        "activated_at": row.activated_at,
    }


def _definition_payload(item, definition: PromptDefinition | None) -> dict:
    versions = list(definition.versions) if definition is not None else []
    active = next((row for row in versions if row.status == "active"), None)
    return {
        "key": item.key,
        "description": item.description,
        "required_variables": list(item.variables),
        "active_version": active.version if active else item.version,
        "active_source": "workspace" if active else "builtin",
        "active_content_hash": active.content_hash if active else item.content_hash,
        "versions": [
            _builtin_payload(item, active=active is None),
            *[_version_payload(row) for row in versions],
        ],
    }


async def _definitions(workspace_id: uuid.UUID, db: AsyncSession) -> dict[str, PromptDefinition]:
    rows = list(
        await db.scalars(
            select(PromptDefinition)
            .options(selectinload(PromptDefinition.versions))
            .where(PromptDefinition.workspace_id == workspace_id)
        )
    )
    return {row.key: row for row in rows}


async def _definition(
    workspace_id: uuid.UUID, key: str, db: AsyncSession
) -> PromptDefinition | None:
    return await db.scalar(
        select(PromptDefinition)
        .options(selectinload(PromptDefinition.versions))
        .where(
            PromptDefinition.workspace_id == workspace_id,
            PromptDefinition.key == key,
        )
    )


async def _locked_definition_versions(
    workspace_id: uuid.UUID, key: str, db: AsyncSession
) -> tuple[PromptDefinition | None, list[PromptVersion]]:
    definition = await db.scalar(
        select(PromptDefinition)
        .where(
            PromptDefinition.workspace_id == workspace_id,
            PromptDefinition.key == key,
        )
        .with_for_update()
    )
    if definition is None:
        return None, []
    versions = list(
        await db.scalars(
            select(PromptVersion)
            .where(PromptVersion.definition_id == definition.id)
            .order_by(PromptVersion.version.desc())
            .with_for_update()
        )
    )
    return definition, versions


def _builtin_or_404(key: str):
    try:
        return get_builtin_prompt(key)
    except PromptRegistryError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt key not found") from None


@router.get(
    "/workspaces/{workspace_id}/prompts",
    response_model=list[PromptDefinitionResponse],
)
async def list_prompts(
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    definitions = await _definitions(workspace.id, db)
    return [_definition_payload(item, definitions.get(item.key)) for item in list_builtin_prompts()]


@router.get(
    "/workspaces/{workspace_id}/prompts/{prompt_key}",
    response_model=PromptDefinitionResponse,
)
async def get_prompt(
    prompt_key: str,
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> dict:
    item = _builtin_or_404(prompt_key)
    return _definition_payload(item, await _definition(workspace.id, prompt_key, db))


@router.post(
    "/workspaces/{workspace_id}/prompts/{prompt_key}/versions",
    response_model=PromptDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt_version(
    prompt_key: str,
    body: PromptVersionCreate,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    item = _builtin_or_404(prompt_key)
    try:
        variables = template_variables(body.template)
    except PromptRegistryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if set(variables) != set(item.variables):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "message": "Template variables must match the built-in contract",
                "required": list(item.variables),
                "received": list(variables),
            },
        )

    definition = await _definition(workspace.id, prompt_key, db)
    if definition is None:
        definition = PromptDefinition(
            workspace_id=workspace.id,
            key=prompt_key,
            description=item.description,
            created_by_id=user.id,
        )
        db.add(definition)
        await db.flush()
    next_version = (
        await db.scalar(
            select(func.max(PromptVersion.version)).where(
                PromptVersion.definition_id == definition.id
            )
        )
        or item.version
    ) + 1
    row = PromptVersion(
        definition_id=definition.id,
        version=next_version,
        status="draft",
        template=body.template,
        variables=list(variables),
        content_hash=prompt_hash(body.template),
        notes=body.notes,
        metadata_json=body.metadata,
        created_by_id=user.id,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An identical prompt version already exists",
        ) from None
    definition = await _definition(workspace.id, prompt_key, db)
    return _definition_payload(item, definition)


@router.post(
    "/workspaces/{workspace_id}/prompts/{prompt_key}/versions/{version}/activate",
    response_model=PromptDefinitionResponse,
)
async def activate_prompt_version(
    prompt_key: str,
    version: int,
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> dict:
    item = _builtin_or_404(prompt_key)
    definition, versions = await _locked_definition_versions(workspace.id, prompt_key, db)
    if definition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt version not found")
    target = next((row for row in versions if row.version == version), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt version not found")
    for row in versions:
        if row.status == "active" and row.id != target.id:
            row.status = "archived"
    await db.flush()
    target.status = "active"
    target.activated_at = datetime.now(UTC)
    await db.commit()
    clear_prompt_cache(workspace.id)
    definition = await _definition(workspace.id, prompt_key, db)
    return _definition_payload(item, definition)


@router.post(
    "/workspaces/{workspace_id}/prompts/{prompt_key}/reset-to-builtin",
    response_model=PromptDefinitionResponse,
)
async def reset_prompt_to_builtin(
    prompt_key: str,
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> dict:
    item = _builtin_or_404(prompt_key)
    definition, versions = await _locked_definition_versions(workspace.id, prompt_key, db)
    if definition is not None:
        for row in versions:
            if row.status == "active":
                row.status = "archived"
        await db.commit()
    clear_prompt_cache(workspace.id)
    definition = await _definition(workspace.id, prompt_key, db)
    return _definition_payload(item, definition)
