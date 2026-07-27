import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatSessionResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    citations: list[dict[str, Any]] | None
    usage: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
