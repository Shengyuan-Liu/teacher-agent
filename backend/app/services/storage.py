"""Uploaded files on disk.

Originals are kept after ingestion because re-ingesting needs them: switching
PDF converter or chunking strategy replays the pipeline rather than asking the
user to upload again.
"""

import shutil
import uuid
from pathlib import Path

from app.core.config import settings


def workspace_dir(workspace_id: uuid.UUID) -> Path:
    return Path(settings.storage_dir) / str(workspace_id)


def source_path(workspace_id: uuid.UUID, source_id: uuid.UUID, suffix: str) -> Path:
    """Named by id, not by the uploaded filename, which is attacker-controlled."""
    directory = workspace_dir(workspace_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{source_id}{suffix}"


def image_dir(workspace_id: uuid.UUID, source_id: uuid.UUID) -> Path:
    return workspace_dir(workspace_id) / "images" / str(source_id)


def save_image(workspace_id: uuid.UUID, source_id: uuid.UUID, image_id: str, data: bytes) -> Path:
    directory = image_dir(workspace_id, source_id)
    directory.mkdir(parents=True, exist_ok=True)
    # image_id comes from the OCR service; keep only the basename so it cannot
    # escape the directory.
    path = directory / Path(image_id).name
    path.write_bytes(data)
    return path


def delete_workspace_files(workspace_id: uuid.UUID) -> None:
    shutil.rmtree(workspace_dir(workspace_id), ignore_errors=True)


def delete_source_files(workspace_id: uuid.UUID, source_id: uuid.UUID, file_path: str) -> None:
    """Remove an original/snapshot and any OCR figures owned by one source."""
    Path(file_path).unlink(missing_ok=True)
    shutil.rmtree(image_dir(workspace_id, source_id), ignore_errors=True)
