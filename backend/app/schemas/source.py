import uuid
from datetime import datetime

from pydantic import BaseModel, HttpUrl

from app.models.source import SourceStatus, SourceType


class UrlSourceCreate(BaseModel):
    url: HttpUrl


class GithubSourceCreate(BaseModel):
    repo_url: HttpUrl


class SourceResponse(BaseModel):
    id: uuid.UUID
    type: SourceType
    title: str
    origin: str | None
    status: SourceStatus
    error: str | None
    progress: float
    progress_detail: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
