import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MemoryKind = Literal["preference", "background", "goal"]


class MemoryCreate(BaseModel):
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=2_000)
    expires_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def content_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Memory content cannot be blank")
        return value.strip()


class MemoryUpdate(BaseModel):
    kind: MemoryKind | None = None
    content: str | None = Field(default=None, min_length=1, max_length=2_000)
    expires_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def updated_content_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Memory content cannot be blank")
        return value.strip() if value is not None else None


class MemoryResponse(BaseModel):
    id: uuid.UUID
    kind: MemoryKind
    memory_key: str
    content: str
    confidence: float
    effective_confidence: float
    importance: float
    user_confirmed: bool
    expires_at: datetime | None
    last_accessed_at: datetime | None
    access_count: int
    source_workspace_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
