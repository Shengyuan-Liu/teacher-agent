"""Phase 3 assessment, review scheduling and mastery persistence."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Assessment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assessments"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="in_progress", nullable=False)
    time_limit_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score: Mapped[float | None] = mapped_column(Float)
    max_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    questions: Mapped[list["AssessmentQuestion"]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AssessmentQuestion.position",
    )
    answers: Mapped[list["AssessmentAnswer"]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", passive_deletes=True
    )


class AssessmentQuestion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "assessment_questions"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    # A snapshot keeps submitted tests and review cards stable if the bank item
    # is later edited or deleted.
    question_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    assessment: Mapped["Assessment"] = relationship(back_populates="questions")


class AssessmentAnswer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assessment_answers"
    __table_args__ = (UniqueConstraint("assessment_id", "assessment_question_id"),)

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    assessment_question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_questions.id", ondelete="CASCADE"),
        index=True,
    )
    response: Mapped[Any] = mapped_column(JSONB, nullable=False)
    score_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    feedback: Mapped[str] = mapped_column(Text, default="", nullable=False)
    grader: Mapped[str] = mapped_column(String(20), nullable=False)
    grader_model: Mapped[str | None] = mapped_column(String(200))

    assessment: Mapped["Assessment"] = relationship(back_populates="answers")


class ReviewItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "question_id", name="uq_review_question"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"), index=True
    )
    question_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    topic: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    interval_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ease_factor: Mapped[float] = mapped_column(Float, default=2.5, nullable=False)
    repetitions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_correct: Mapped[bool | None] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TopicMastery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "topic_mastery"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "topic", name="uq_user_topic_mastery"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    topic: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_evidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
