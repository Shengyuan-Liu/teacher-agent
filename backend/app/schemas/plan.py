import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class PlanCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=500)
    daily_minutes: int = Field(ge=10, le=720)
    deadline: date | None = None


class PlanStageResponse(BaseModel):
    id: uuid.UUID
    position: int
    title: str
    description: str
    topics: list[str]
    activities: list[str]
    estimated_minutes: int
    status: str

    model_config = {"from_attributes": True}


class StudyPlanResponse(BaseModel):
    id: uuid.UUID
    goal: str
    daily_minutes: int
    deadline: date | None
    created_at: datetime
    stages: list[PlanStageResponse]

    model_config = {"from_attributes": True}


class StageUpdate(BaseModel):
    status: str = Field(pattern="^(pending|done)$")


class QuizCreate(BaseModel):
    count: int = Field(default=5, ge=1, le=15)
    topic: str | None = Field(default=None, max_length=300)


class QuestionResponse(BaseModel):
    id: uuid.UUID
    type: str
    difficulty: str
    stem: str
    options: list[str] | None
    answer: Any
    explanation: str
    source: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}
