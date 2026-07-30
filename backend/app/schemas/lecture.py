import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class LectureSummary(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    chat_session_id: uuid.UUID
    plan_stage_id: uuid.UUID | None
    title: str
    scope: str
    status: str
    current_section_index: int
    total_sections: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class LectureDetail(LectureSummary):
    outline: list[dict[str, Any]]
    pending_check: dict[str, Any] | None
    section_history: list[dict[str, Any]]
