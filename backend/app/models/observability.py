"""Durable, queryable Agent traces that complement external OTLP export."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="SET NULL"), index=True
    )
    input_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    output_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    replay_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    trace_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    root_span_id: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), default="chat", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(40), index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column("input", JSONB, default=dict, nullable=False)
    output_json: Mapped[dict[str, Any]] = mapped_column(
        "output", JSONB, default=dict, nullable=False
    )
    model_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    replay_of: Mapped["AgentRun | None"] = relationship(
        remote_side="AgentRun.id", foreign_keys=[replay_of_id]
    )
    spans: Mapped[list["AgentSpan"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AgentSpan.ordinal",
    )


class AgentSpan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_spans"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    span_id: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(16))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    agent: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), default="agent", nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(50), index=True)
    model: Mapped[str | None] = mapped_column(String(200), index=True)
    tier: Mapped[str | None] = mapped_column(String(20))
    reasoning_effort: Mapped[str | None] = mapped_column(String(20))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column("input", JSONB, default=dict, nullable=False)
    output_json: Mapped[dict[str, Any]] = mapped_column(
        "output", JSONB, default=dict, nullable=False
    )
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped["AgentRun"] = relationship(back_populates="spans")
