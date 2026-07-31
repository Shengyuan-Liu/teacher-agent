"""Capture one streamed Agent turn as both OTel spans and durable database rows."""

from __future__ import annotations

import contextvars
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import AgentRun, AgentSpan, ChatSession, Message
from app.services import usage
from app.services.providers import IntelligenceTier, model_trace
from app.services.telemetry import tracer
from app.services.trace import trace_value

_replay_source: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "agent_replay_source", default=None
)
CAPTURED_HISTORY_MESSAGES = 6


def set_replay_source(run_id: uuid.UUID):
    return _replay_source.set(run_id)


def reset_replay_source(token) -> None:
    _replay_source.reset(token)


@dataclass
class RecordedSpan:
    otel_span: Span
    span_id: str
    parent_span_id: str
    ordinal: int
    name: str
    agent: str
    stage: str
    label: str
    started_at: datetime
    started_clock: float
    provider: str | None = None
    model: str | None = None
    tier: str | None = None
    reasoning_effort: str | None = None
    status: str = "running"
    output: dict[str, Any] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_ms: float | None = None
    error: str | None = None
    completed_at: datetime | None = None


class AgentTraceRecorder:
    def __init__(
        self,
        *,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        trace_id: str,
        root_span_id: str,
        root_span: Span,
        replay_of_id: uuid.UUID | None,
        started_at: datetime,
        started_clock: float,
        request_id: uuid.UUID | None,
    ) -> None:
        self.run_id = run_id
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.session_id = session_id
        self.trace_id = trace_id
        self.root_span_id = root_span_id
        self.root_span = root_span
        self.root_context = otel_trace.set_span_in_context(root_span)
        self.replay_of_id = replay_of_id
        self.started_at = started_at
        self.started_clock = started_clock
        self.request_id = request_id
        self.spans: list[RecordedSpan] = []
        self.active: list[RecordedSpan] = []
        self.usage_cursor = 0
        self.usage_payload: dict[str, Any] = {}
        self.done_payload: dict[str, Any] = {}
        self.task_dag: dict[str, Any] = {}
        self.intent: str | None = None
        self.error: str | None = None
        self.finished = False

    @classmethod
    async def create(
        cls,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None,
        question: str,
        force_web: bool,
        intent_override: str | None,
        request_id: uuid.UUID | None,
    ) -> AgentTraceRecorder | None:
        if not settings.observability_enabled:
            return None
        async with AsyncSessionLocal() as db:
            session = await db.get(ChatSession, session_id)
            if session is None:
                return None
            effective_user_id = user_id or session.user_id
            recent = list(
                await db.scalars(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.created_at.desc())
                    .limit(CAPTURED_HISTORY_MESSAGES)
                )
            )
            history = [
                {"role": message.role, "content": message.content} for message in reversed(recent)
            ]

            attributes = {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.workflow.name": "teacher.chat",
                "gen_ai.conversation.id": str(session_id),
                "teacher.workspace.id": str(session.workspace_id),
                "teacher.replay": _replay_source.get() is not None,
            }
            root = tracer().start_span(
                "teacher.chat.turn", kind=SpanKind.INTERNAL, attributes=attributes
            )
            context = root.get_span_context()
            trace_id = f"{context.trace_id:032x}" if context.trace_id else uuid.uuid4().hex
            root_span_id = f"{context.span_id:016x}" if context.span_id else uuid.uuid4().hex[:16]
            started_at = datetime.now(UTC)
            replay_of_id = _replay_source.get()
            input_json: dict[str, Any] = {
                "question": question if settings.otel_capture_content else "[REDACTED]",
                "force_web": force_web,
                "intent_override": intent_override,
                "request_id": str(request_id) if request_id else None,
                "history": history if settings.otel_capture_content else [],
            }
            row = AgentRun(
                workspace_id=session.workspace_id,
                user_id=effective_user_id,
                session_id=session_id,
                replay_of_id=replay_of_id,
                trace_id=trace_id,
                root_span_id=root_span_id,
                kind="replay" if replay_of_id else "chat",
                status="running",
                input_json=input_json,
                model_config={
                    "fast": model_trace(IntelligenceTier.FAST),
                    "smart": model_trace(IntelligenceTier.SMART),
                    "embedding_provider": settings.embedding_provider,
                    "embedding_model": settings.embedding_model,
                    "reranker": settings.reranker,
                },
                started_at=started_at,
            )
            db.add(row)
            await db.commit()
            return cls(
                run_id=row.id,
                workspace_id=session.workspace_id,
                user_id=effective_user_id,
                session_id=session_id,
                trace_id=trace_id,
                root_span_id=root_span_id,
                root_span=root,
                replay_of_id=replay_of_id,
                started_at=started_at,
                started_clock=perf_counter(),
                request_id=request_id,
            )

    def start_step(self, payload: dict[str, Any]) -> None:
        stage = str(payload.get("stage") or "step")
        agent = str(payload.get("agent") or "agent")
        label = str(payload.get("label") or stage)
        attributes: dict[str, str] = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": agent,
            "gen_ai.workflow.name": f"teacher.{agent}",
            "teacher.agent.stage": stage,
            "teacher.agent.label": label,
        }
        if payload.get("provider"):
            attributes["gen_ai.provider.name"] = str(payload["provider"])
        if payload.get("model"):
            attributes["gen_ai.request.model"] = str(payload["model"])
        if payload.get("tier"):
            attributes["teacher.model.tier"] = str(payload["tier"])
        span = tracer().start_span(
            f"{agent}.{stage}",
            context=self.root_context,
            kind=SpanKind.INTERNAL,
            attributes=attributes,
        )
        context = span.get_span_context()
        record = RecordedSpan(
            otel_span=span,
            span_id=f"{context.span_id:016x}" if context.span_id else uuid.uuid4().hex[:16],
            parent_span_id=self.root_span_id,
            ordinal=len(self.spans) + 1,
            name=f"{agent}.{stage}",
            agent=agent,
            stage=stage,
            label=label,
            started_at=datetime.now(UTC),
            started_clock=perf_counter(),
            provider=payload.get("provider"),
            model=payload.get("model"),
            tier=payload.get("tier"),
            reasoning_effort=payload.get("reasoning_effort"),
        )
        self.spans.append(record)
        self.active.append(record)

    def activate(self):
        """Make auto-instrumented HTTP/DB spans descendants of the Agent root."""
        return otel_context.attach(self.root_context)

    @staticmethod
    def deactivate(token) -> None:
        otel_context.detach(token)

    def _new_usage(self) -> tuple[int, int, float | None]:
        ledger = usage.current()
        if ledger is None:
            return 0, 0, None
        calls = ledger.calls[self.usage_cursor :]
        self.usage_cursor = len(ledger.calls)
        costs = [call.cost_usd for call in calls if call.cost_usd is not None]
        return (
            sum(call.input_tokens for call in calls),
            sum(call.output_tokens for call in calls),
            round(sum(costs), 6) if costs else None,
        )

    def finish_step(self, payload: dict[str, Any]) -> None:
        stage = str(payload.get("stage") or "step")
        record = next((item for item in reversed(self.active) if item.stage == stage), None)
        if record is None:
            return
        self.active.remove(record)
        result = trace_value(payload.get("result"))
        record.output = {"result": result}
        record.input_tokens, record.output_tokens, record.cost_usd = self._new_usage()
        record.status = "completed"
        record.completed_at = datetime.now(UTC)
        record.latency_ms = round((perf_counter() - record.started_clock) * 1000, 3)
        record.otel_span.set_attribute("gen_ai.usage.input_tokens", record.input_tokens)
        record.otel_span.set_attribute("gen_ai.usage.output_tokens", record.output_tokens)
        record.otel_span.set_attribute(
            "teacher.output.size_bytes",
            len(json.dumps(result, ensure_ascii=False, default=str).encode()),
        )
        record.otel_span.set_status(Status(StatusCode.OK))
        record.otel_span.end()

        if stage == "router" and isinstance(result, dict):
            self.intent = result.get("intent")
            if isinstance(result.get("dag"), dict):
                self.task_dag = result["dag"]
        elif isinstance(result, dict) and isinstance(result.get("dag"), dict):
            self.task_dag = result["dag"]

    def consume_event(self, event: dict[str, Any]) -> None:
        try:
            payload = json.loads(event.get("data") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        name = event.get("event")
        if name == "stage":
            self.start_step(payload)
        elif name == "stage_result":
            self.finish_step(payload)
        elif name == "usage":
            self.usage_payload = payload
        elif name == "done":
            self.done_payload = payload
        elif name == "error":
            self.error = str(payload.get("message") or "Agent stream failed")[:4000]

    def _close_active(self, status: str, error: str | None) -> None:
        for record in list(self.active):
            record.status = status
            record.error = error
            record.completed_at = datetime.now(UTC)
            record.latency_ms = round((perf_counter() - record.started_clock) * 1000, 3)
            if error:
                record.otel_span.record_exception(RuntimeError(error))
                record.otel_span.set_status(Status(StatusCode.ERROR, error[:255]))
            record.otel_span.end()
        self.active.clear()

    async def finish(self, *, cancelled: bool = False) -> None:
        if self.finished:
            return
        self.finished = True
        status = "cancelled" if cancelled else "error" if self.error else "completed"
        if not self.done_payload and status == "completed":
            status = "error"
            self.error = "Agent stream ended without a completion event"
        self._close_active(status, self.error)
        completed_at = datetime.now(UTC)
        latency_ms = round((perf_counter() - self.started_clock) * 1000, 3)

        if self.error:
            self.root_span.record_exception(RuntimeError(self.error))
            self.root_span.set_status(Status(StatusCode.ERROR, self.error[:255]))
        else:
            self.root_span.set_status(Status(StatusCode.OK))
        self.root_span.set_attribute("teacher.agent.status", status)
        self.root_span.set_attribute("teacher.agent.intent", self.intent or "unknown")
        self.root_span.set_attribute(
            "gen_ai.usage.input_tokens", int(self.usage_payload.get("input_tokens", 0))
        )
        self.root_span.set_attribute(
            "gen_ai.usage.output_tokens", int(self.usage_payload.get("output_tokens", 0))
        )
        self.root_span.end()

        async with AsyncSessionLocal() as db:
            row = await db.get(AgentRun, self.run_id)
            if row is None:
                return
            input_message = (
                await db.scalar(select(Message).where(Message.client_request_id == self.request_id))
                if self.request_id
                else None
            )
            message_id = self.done_payload.get("message_id")
            output_message = await db.get(Message, uuid.UUID(message_id)) if message_id else None
            row.input_message_id = input_message.id if input_message else None
            row.output_message_id = output_message.id if output_message else None
            row.status = status
            if self.done_payload.get("duplicate") and self.replay_of_id is None:
                row.kind = "idempotency_replay"
            row.intent = self.intent
            row.usage = self.usage_payload
            row.latency_ms = latency_ms
            row.error = self.error
            row.completed_at = completed_at
            row.output_json = {
                "done": self.done_payload,
                "task_dag": self.done_payload.get("task_dag") or self.task_dag,
                "content": (
                    output_message.content
                    if output_message is not None and settings.otel_capture_content
                    else None
                ),
                "citations": output_message.citations if output_message is not None else None,
                "web_citations": (
                    output_message.web_citations if output_message is not None else []
                ),
                "artifacts": output_message.artifacts if output_message is not None else {},
            }
            db.add(
                AgentSpan(
                    run_id=row.id,
                    trace_id=self.trace_id,
                    span_id=self.root_span_id,
                    parent_span_id=None,
                    ordinal=0,
                    name="teacher.chat.turn",
                    agent="orchestrator",
                    stage="turn",
                    kind="workflow",
                    status=status,
                    attributes={
                        "replay": self.replay_of_id is not None,
                        "intent": self.intent,
                    },
                    input_json=row.input_json,
                    output_json=row.output_json,
                    input_tokens=int(self.usage_payload.get("input_tokens", 0)),
                    output_tokens=int(self.usage_payload.get("output_tokens", 0)),
                    cost_usd=self.usage_payload.get("cost_usd"),
                    latency_ms=latency_ms,
                    error=self.error,
                    started_at=self.started_at,
                    completed_at=completed_at,
                )
            )
            for record in self.spans:
                db.add(
                    AgentSpan(
                        run_id=row.id,
                        trace_id=self.trace_id,
                        span_id=record.span_id,
                        parent_span_id=record.parent_span_id,
                        ordinal=record.ordinal,
                        name=record.name,
                        agent=record.agent,
                        stage=record.stage,
                        kind="agent",
                        status=record.status,
                        provider=record.provider,
                        model=record.model,
                        tier=record.tier,
                        reasoning_effort=record.reasoning_effort,
                        attributes={"label": record.label},
                        output_json=record.output,
                        input_tokens=record.input_tokens,
                        output_tokens=record.output_tokens,
                        cost_usd=record.cost_usd,
                        latency_ms=record.latency_ms,
                        error=record.error,
                        started_at=record.started_at,
                        completed_at=record.completed_at,
                    )
                )
            for index, call in enumerate(self.usage_payload.get("calls", []), start=1):
                db.add(
                    AgentSpan(
                        run_id=row.id,
                        trace_id=self.trace_id,
                        span_id=uuid.uuid4().hex[:16],
                        parent_span_id=self.root_span_id,
                        ordinal=len(self.spans) + index,
                        name=f"model.{call.get('step', 'call')}",
                        agent=str(call.get("step") or "model"),
                        stage=str(call.get("step") or "call"),
                        kind="model_call",
                        status="completed",
                        model=call.get("model"),
                        attributes={"accounting_source": "usage_ledger"},
                        input_tokens=int(call.get("input_tokens", 0)),
                        output_tokens=int(call.get("output_tokens", 0)),
                        cost_usd=call.get("cost_usd"),
                        started_at=completed_at,
                        completed_at=completed_at,
                    )
                )
            await db.commit()
