"""Keep the workspace ingestion state derived from its sources.

The source rows are the system of record.  Centralising this calculation keeps
uploads, retries, worker failures and deletions from leaving a workspace stuck
in ``ingesting``.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source, SourceStatus, Workspace, WorkspaceStatus

ACTIVE_SOURCE_STATUSES = {
    SourceStatus.PENDING,
    SourceStatus.PARSING,
    SourceStatus.EMBEDDING,
}


def derive_workspace_status(statuses: Iterable[SourceStatus]) -> WorkspaceStatus:
    values = list(statuses)
    if not values:
        return WorkspaceStatus.EMPTY
    if any(value in ACTIVE_SOURCE_STATUSES for value in values):
        return WorkspaceStatus.INGESTING
    if all(value is SourceStatus.READY for value in values):
        return WorkspaceStatus.READY
    return WorkspaceStatus.PARTIAL


async def refresh_workspace_status(db: AsyncSession, workspace_id: uuid.UUID) -> WorkspaceStatus:
    statuses = await db.scalars(select(Source.status).where(Source.workspace_id == workspace_id))
    status = derive_workspace_status(statuses)
    workspace = await db.get(Workspace, workspace_id)
    if workspace is not None:
        workspace.status = status
    return status
