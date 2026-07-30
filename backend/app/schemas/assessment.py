import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AssessmentCreate(BaseModel):
    title: str = Field(default="Knowledge check", min_length=1, max_length=300)
    count: int = Field(default=10, ge=1, le=50)
    time_limit_minutes: int = Field(default=20, ge=1, le=240)
    topic: str | None = Field(default=None, max_length=300)


class AssessmentSubmit(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)


class AssessmentQuestionResponse(BaseModel):
    id: uuid.UUID
    position: int
    points: float
    type: str
    difficulty: str
    stem: str
    options: list[str] | None
    source: dict[str, Any] | None
    response: Any | None = None
    score_fraction: float | None = None
    correct: bool | None = None
    feedback: str | None = None
    grader: str | None = None
    grader_model: str | None = None
    answer: Any | None = None
    explanation: str | None = None


class AssessmentResponse(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    time_limit_minutes: int
    started_at: datetime
    submitted_at: datetime | None
    score: float | None
    max_score: float
    created_at: datetime
    questions: list[AssessmentQuestionResponse] = Field(default_factory=list)


class AssessmentSummary(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    time_limit_minutes: int
    started_at: datetime
    submitted_at: datetime | None
    score: float | None
    max_score: float
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewSubmit(BaseModel):
    response: Any


class ReviewItemResponse(BaseModel):
    id: uuid.UUID
    topic: str
    due_at: datetime
    interval_days: int
    repetitions: int
    last_correct: bool | None
    question: dict[str, Any]


class ReviewResult(BaseModel):
    item: ReviewItemResponse
    score_fraction: float
    correct: bool
    feedback: str
    grader: str
    grader_model: str | None = None


class MasteryResponse(BaseModel):
    topic: str
    score: float
    attempts: int
    correct_count: int
    last_evidence: float
    updated_at: datetime

    model_config = {"from_attributes": True}
