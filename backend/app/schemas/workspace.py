import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.workspace import WorkspaceStatus


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    language: str = "zh-CN"


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    language: str
    status: WorkspaceStatus
    created_at: datetime

    model_config = {"from_attributes": True}
