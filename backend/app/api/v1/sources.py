import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_owned_workspace
from app.core.config import settings
from app.core.database import get_db
from app.models import Source, SourceStatus, SourceType, Workspace
from app.models.workspace import WorkspaceStatus
from app.rag.repo import parse_repo_url
from app.schemas.source import GithubSourceCreate, SourceResponse, UrlSourceCreate
from app.services.queue import get_queue
from app.services.storage import source_path

router = APIRouter(prefix="/workspaces/{workspace_id}/sources", tags=["sources"])

EXTENSIONS = {
    ".pdf": SourceType.PDF,
    ".md": SourceType.MARKDOWN,
    ".markdown": SourceType.MARKDOWN,
    ".docx": SourceType.DOCX,
    ".pptx": SourceType.PPTX,
    ".xlsx": SourceType.XLSX,
}


@router.post("/upload", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def upload_source(
    file: UploadFile,
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> Source:
    suffix = Path(file.filename or "").suffix.lower()
    source_type = EXTENSIONS.get(suffix)
    if source_type is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type '{suffix}', expected one of: {', '.join(EXTENSIONS)}",
        )

    content = await file.read()
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"File exceeds the {settings.max_upload_size_mb} MB limit",
        )

    source = Source(
        workspace_id=workspace.id,
        type=source_type,
        title=file.filename or "untitled",
        file_path="",
    )
    db.add(source)
    await db.flush()

    dest = source_path(workspace.id, source.id, suffix)
    dest.write_bytes(content)
    source.file_path = str(dest)

    workspace.status = WorkspaceStatus.INGESTING
    await db.commit()

    queue = await get_queue()
    await queue.enqueue_job("ingest_source", str(source.id))
    return source


async def _create_remote_source(
    db: AsyncSession,
    workspace: Workspace,
    source_type: SourceType,
    origin: str,
    title: str,
) -> Source:
    source = Source(
        workspace_id=workspace.id,
        type=source_type,
        title=title[:300],
        origin=origin,
        file_path="",
    )
    db.add(source)
    await db.flush()
    source.file_path = str(source_path(workspace.id, source.id, ".md"))
    workspace.status = WorkspaceStatus.INGESTING
    await db.commit()
    queue = await get_queue()
    await queue.enqueue_job("ingest_source", str(source.id))
    return source


@router.post("/url", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def add_url_source(
    body: UrlSourceCreate,
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> Source:
    seed = str(body.url)
    parts = urlsplit(seed)
    title = parts.netloc + (parts.path.rstrip("/") or "")
    return await _create_remote_source(db, workspace, SourceType.URL, seed, title)


@router.post("/github", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def add_github_source(
    body: GithubSourceCreate,
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> Source:
    try:
        owner, name = parse_repo_url(str(body.repo_url))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return await _create_remote_source(
        db, workspace, SourceType.GITHUB, str(body.repo_url), f"{owner}/{name}"
    )


@router.get("", response_model=list[SourceResponse])
async def list_sources(
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> list[Source]:
    result = await db.scalars(
        select(Source).where(Source.workspace_id == workspace.id).order_by(Source.created_at)
    )
    return list(result)


@router.post("/{source_id}/retry", response_model=SourceResponse)
async def retry_source(
    source_id: uuid.UUID,
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> Source:
    source = await db.get(Source, source_id)
    if source is None or source.workspace_id != workspace.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    source.status = SourceStatus.PENDING
    source.error = None
    await db.commit()
    queue = await get_queue()
    await queue.enqueue_job("ingest_source", str(source.id))
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID,
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> None:
    source = await db.get(Source, source_id)
    if source is None or source.workspace_id != workspace.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    Path(source.file_path).unlink(missing_ok=True)  # noqa: ASYNC240 - tiny local file
    await db.delete(source)
