"""Public contracts for workspace prompt version management."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PromptVersionCreate(BaseModel):
    template: str = Field(min_length=1, max_length=100_000)
    notes: str | None = Field(default=None, max_length=2_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromptVersionResponse(BaseModel):
    id: uuid.UUID | None = None
    version: int
    status: Literal["builtin", "draft", "active", "archived"]
    template: str
    variables: list[str]
    content_hash: str
    source: Literal["builtin", "workspace"]
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    activated_at: datetime | None = None


class PromptDefinitionResponse(BaseModel):
    key: str
    description: str
    required_variables: list[str]
    active_version: int
    active_source: Literal["builtin", "workspace"]
    active_content_hash: str
    versions: list[PromptVersionResponse]
