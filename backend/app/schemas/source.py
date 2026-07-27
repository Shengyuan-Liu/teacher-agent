import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.source import SourceStatus, SourceType


class SourceResponse(BaseModel):
    id: uuid.UUID
    type: SourceType
    title: str
    status: SourceStatus
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
