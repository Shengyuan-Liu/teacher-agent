import json
import math
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_current_user, get_owned_workspace
from app.core.database import AsyncSessionLocal, get_db
from app.models import AgentRun, AgentSpan, ChatSession, Message, User, Workspace
from app.schemas.observability import (
    AgentRunResponse,
    ObservabilitySummary,
    ReplayRequest,
)
from app.services.agent_observability import reset_replay_source, set_replay_source
from app.services.chat_stream import stream_answer

router = APIRouter(tags=["observability"])


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _run_payload(row: AgentRun, *, detail: bool = False) -> dict[str, Any]:
    comparison = None
    if row.replay_of is not None:
        before = row.replay_of
        before_usage = before.usage or {}
        current_usage = row.usage or {}
        comparison = {
            "source_run_id": str(before.id),
            "latency_delta_ms": (
                round((row.latency_ms or 0) - (before.latency_ms or 0), 3)
                if row.latency_ms is not None and before.latency_ms is not None
                else None
            ),
            "input_tokens_delta": int(current_usage.get("input_tokens", 0))
            - int(before_usage.get("input_tokens", 0)),
            "output_tokens_delta": int(current_usage.get("output_tokens", 0))
            - int(before_usage.get("output_tokens", 0)),
            "cost_delta_usd": (
                round(
                    float(current_usage["cost_usd"]) - float(before_usage["cost_usd"]),
                    6,
                )
                if current_usage.get("cost_usd") is not None
                and before_usage.get("cost_usd") is not None
                else None
            ),
            "output_changed": row.output_json.get("content") != before.output_json.get("content"),
        }
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "session_id": row.session_id,
        "replay_of_id": row.replay_of_id,
        "trace_id": row.trace_id,
        "root_span_id": row.root_span_id,
        "kind": row.kind,
        "status": row.status,
        "intent": row.intent,
        "input": row.input_json,
        "output": row.output_json,
        "model_config": row.model_config,
        "usage": row.usage,
        "latency_ms": row.latency_ms,
        "error": row.error,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
        "spans": [
            {
                "id": span.id,
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "parent_span_id": span.parent_span_id,
                "ordinal": span.ordinal,
                "name": span.name,
                "agent": span.agent,
                "stage": span.stage,
                "kind": span.kind,
                "status": span.status,
                "provider": span.provider,
                "model": span.model,
                "tier": span.tier,
                "reasoning_effort": span.reasoning_effort,
                "attributes": span.attributes,
                "input": span.input_json,
                "output": span.output_json,
                "input_tokens": span.input_tokens,
                "output_tokens": span.output_tokens,
                "cost_usd": span.cost_usd,
                "latency_ms": span.latency_ms,
                "error": span.error,
                "started_at": span.started_at,
                "completed_at": span.completed_at,
            }
            for span in row.spans
        ]
        if detail
        else None,
        "replay_comparison": comparison,
    }


async def _owned_run(
    run_id: uuid.UUID, workspace: Workspace, user: User, db: AsyncSession
) -> AgentRun:
    row = await db.scalar(
        select(AgentRun)
        .options(
            selectinload(AgentRun.spans),
            selectinload(AgentRun.replay_of),
        )
        .where(
            AgentRun.id == run_id,
            AgentRun.workspace_id == workspace.id,
            AgentRun.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent run not found")
    return row


def _breakdowns(spans: list[AgentSpan], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[AgentSpan]] = defaultdict(list)
    expected_kind = "model_call" if field == "model" else "agent"
    for span in spans:
        name = getattr(span, field)
        if name and span.kind == expected_kind:
            groups[str(name)].append(span)
    rows = []
    for name, items in groups.items():
        latencies = [item.latency_ms for item in items if item.latency_ms is not None]
        costs = [item.cost_usd for item in items if item.cost_usd is not None]
        rows.append(
            {
                "name": name,
                "calls": len(items),
                "errors": sum(item.status != "completed" for item in items),
                "p50_latency_ms": _percentile(latencies, 0.5),
                "p95_latency_ms": _percentile(latencies, 0.95),
                "input_tokens": sum(item.input_tokens for item in items),
                "output_tokens": sum(item.output_tokens for item in items),
                "cost_usd": round(sum(costs), 6) if costs else None,
            }
        )
    return sorted(rows, key=lambda item: (-item["calls"], item["name"]))


@router.get(
    "/workspaces/{workspace_id}/observability/summary",
    response_model=ObservabilitySummary,
)
async def observability_summary(
    hours: int = Query(default=24, ge=1, le=24 * 30),
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    runs = list(
        await db.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace.id,
                AgentRun.user_id == user.id,
                AgentRun.started_at >= since,
            )
        )
    )
    spans = list(
        await db.scalars(
            select(AgentSpan)
            .join(AgentRun, AgentSpan.run_id == AgentRun.id)
            .where(
                AgentRun.workspace_id == workspace.id,
                AgentRun.user_id == user.id,
                AgentRun.started_at >= since,
            )
        )
    )
    latencies = [run.latency_ms for run in runs if run.latency_ms is not None]
    costs = [run.usage.get("cost_usd") for run in runs if run.usage.get("cost_usd") is not None]
    completed = sum(run.status == "completed" for run in runs)
    return {
        "window_hours": hours,
        "runs": len(runs),
        "completed": completed,
        "errors": sum(run.status in ("error", "cancelled") for run in runs),
        "success_rate": round(completed / len(runs), 6) if runs else 0.0,
        "p50_latency_ms": _percentile(latencies, 0.5),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "input_tokens": sum(int(run.usage.get("input_tokens", 0)) for run in runs),
        "output_tokens": sum(int(run.usage.get("output_tokens", 0)) for run in runs),
        "cost_usd": round(sum(float(value) for value in costs), 6) if costs else None,
        "by_agent": _breakdowns(spans, "agent"),
        "by_model": _breakdowns(spans, "model"),
    }


@router.get(
    "/workspaces/{workspace_id}/observability/runs",
    response_model=list[AgentRunResponse],
)
async def list_agent_runs(
    run_status: str | None = Query(default=None, alias="status"),
    intent: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    query = (
        select(AgentRun)
        .options(selectinload(AgentRun.replay_of))
        .where(AgentRun.workspace_id == workspace.id, AgentRun.user_id == user.id)
    )
    if run_status:
        query = query.where(AgentRun.status == run_status)
    if intent:
        query = query.where(AgentRun.intent == intent)
    rows = list(await db.scalars(query.order_by(AgentRun.started_at.desc()).limit(limit)))
    return [_run_payload(row) for row in rows]


@router.get(
    "/workspaces/{workspace_id}/observability/runs/{run_id}",
    response_model=AgentRunResponse,
)
async def get_agent_run(
    run_id: uuid.UUID,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return _run_payload(await _owned_run(run_id, workspace, user, db), detail=True)


async def _replay_stream(
    source: AgentRun,
    workspace: Workspace,
    user: User,
    body: ReplayRequest,
) -> AsyncGenerator[dict[str, str], None]:
    question = source.input_json.get("question")
    if not isinstance(question, str) or question == "[REDACTED]":
        yield {
            "event": "error",
            "data": json.dumps({"message": "This run was captured without replayable content"}),
        }
        return

    async with AsyncSessionLocal() as db:
        session = ChatSession(
            workspace_id=workspace.id,
            user_id=user.id,
            title=f"Replay {source.trace_id[:8]}",
        )
        db.add(session)
        await db.flush()
        for item in source.input_json.get("history", []):
            role = item.get("role")
            content = item.get("content")
            if role in ("user", "assistant") and isinstance(content, str):
                db.add(Message(session_id=session.id, role=role, content=content))
        await db.commit()
        session_id = session.id

    token = set_replay_source(source.id)
    try:
        original_intent = source.input_json.get("intent_override")
        intent = body.intent or original_intent
        force_web = (
            body.force_web
            if body.force_web is not None
            else bool(source.input_json.get("force_web", False))
        )
        async for event in stream_answer(
            session_id,
            question,
            force_web,
            user.id,
            intent,
            uuid.uuid4(),
        ):
            yield event
    finally:
        reset_replay_source(token)
        async with AsyncSessionLocal() as db:
            temporary = await db.get(ChatSession, session_id)
            if temporary is not None:
                await db.delete(temporary)
                await db.commit()


@router.post("/workspaces/{workspace_id}/observability/runs/{run_id}/replay/stream")
async def replay_agent_run(
    body: ReplayRequest,
    run_id: uuid.UUID,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    source = await _owned_run(run_id, workspace, user, db)
    if source.status != "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only completed Agent runs can be replayed")
    if source.input_json.get("question") == "[REDACTED]":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This run was captured without replayable content"
        )
    return EventSourceResponse(_replay_stream(source, workspace, user, body))
