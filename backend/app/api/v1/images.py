import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.api.deps import get_owned_workspace
from app.models import Workspace
from app.services.storage import image_dir

router = APIRouter(prefix="/workspaces/{workspace_id}/images", tags=["images"])


@router.get("/{source_id}/{image_id}")
async def get_image(
    source_id: uuid.UUID,
    image_id: str,
    workspace: Workspace = Depends(get_owned_workspace),
) -> FileResponse:
    """Figures extracted during ingestion, scoped to the owning workspace."""
    path = image_dir(workspace.id, source_id) / Path(image_id).name
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})
