import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.router import Intent


class AgentSpanResponse(BaseModel):
    id: uuid.UUID
    trace_id: str
    span_id: str
    parent_span_id: str | None
    ordinal: int
    name: str
    agent: str
    stage: str
    kind: str
    status: str
    provider: str | None
    model: str | None
    tier: str | None
    reasoning_effort: str | None
    attributes: dict[str, Any]
    input: dict[str, Any]
    output: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_ms: float | None
    error: str | None
    started_at: datetime
    completed_at: datetime | None


class AgentRunResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    session_id: uuid.UUID | None
    replay_of_id: uuid.UUID | None
    trace_id: str
    root_span_id: str
    kind: str
    status: str
    intent: str | None
    input: dict[str, Any]
    output: dict[str, Any]
    model_configuration: dict[str, Any] = Field(alias="model_config")
    usage: dict[str, Any]
    latency_ms: float | None
    error: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    spans: list[AgentSpanResponse] | None = None
    replay_comparison: dict[str, Any] | None = None


class ObservabilityBreakdown(BaseModel):
    name: str
    calls: int
    errors: int
    p50_latency_ms: float
    p95_latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


class ObservabilitySummary(BaseModel):
    window_hours: int
    runs: int
    completed: int
    errors: int
    success_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    by_agent: list[ObservabilityBreakdown]
    by_model: list[ObservabilityBreakdown]


class ReplayRequest(BaseModel):
    intent: Intent | None = None
    force_web: bool | None = None
    prompt_mode: Literal["current", "original"] = "current"
    note: str | None = Field(default=None, max_length=500)
