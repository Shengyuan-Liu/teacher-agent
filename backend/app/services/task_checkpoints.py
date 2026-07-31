"""PostgreSQL-backed leases and node checkpoints for Typed Task DAGs."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.agents.task_dag import (
    AgentTask,
    TaskCheckpointSnapshot,
    TaskDAG,
    TaskDAGExecutionBusy,
    TaskStatus,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import TaskExecution, TaskNodeCheckpoint
from app.services.trace import trace_value


def _definition(dag: TaskDAG) -> dict[str, Any]:
    return dag.as_payload()


def _definition_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def load_persisted_task_dag(execution_key: uuid.UUID) -> TaskDAG | None:
    """Return the original router graph so a retry cannot drift to a new plan."""

    async with AsyncSessionLocal() as db:
        payload = await db.scalar(
            select(TaskExecution.dag).where(TaskExecution.execution_key == execution_key)
        )
    return TaskDAG.from_payload(payload) if isinstance(payload, dict) else None


class PostgresTaskCheckpointStore:
    """A run-level lease plus materialized node results.

    The lease prevents concurrent retries from issuing duplicate provider calls.
    A worker that takes over an expired/interrupted run keeps completed results
    and only executes unfinished nodes.
    """

    def __init__(
        self,
        *,
        execution_key: uuid.UUID,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
        lease_seconds: int | None = None,
    ) -> None:
        self.execution_key = execution_key
        self.workspace_id = workspace_id
        self.session_id = session_id
        self.lease_seconds = lease_seconds or settings.task_dag_lease_seconds
        self.owner = uuid.uuid4().hex
        self.execution_id: uuid.UUID | None = None
        self._finished = False

    def _lease_deadline(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self.lease_seconds)

    async def load(self, dag: TaskDAG) -> TaskCheckpointSnapshot:
        definition = _definition(dag)
        dag_hash = _definition_hash(definition)
        for _attempt in range(2):
            async with AsyncSessionLocal() as db:
                row = await db.scalar(
                    select(TaskExecution)
                    .where(TaskExecution.execution_key == self.execution_key)
                    .with_for_update()
                )
                created = row is None
                if row is None:
                    row = TaskExecution(
                        execution_key=self.execution_key,
                        workspace_id=self.workspace_id,
                        session_id=self.session_id,
                        dag_hash=dag_hash,
                        dag=definition,
                        status="running",
                        lease_owner=self.owner,
                        lease_expires_at=self._lease_deadline(),
                    )
                    db.add(row)
                    try:
                        await db.flush()
                    except IntegrityError:
                        await db.rollback()
                        continue
                    for node in dag.nodes:
                        db.add(
                            TaskNodeCheckpoint(
                                execution_id=row.id,
                                task_id=node.id,
                                agent=node.agent,
                                status="pending",
                            )
                        )
                    await db.commit()
                else:
                    if row.dag_hash != dag_hash:
                        raise ValueError(
                            "The durable execution graph differs from its original checkpoint"
                        )
                    now = datetime.now(UTC)
                    if (
                        row.lease_owner
                        and row.lease_owner != self.owner
                        and row.lease_expires_at
                        and row.lease_expires_at > now
                    ):
                        raise TaskDAGExecutionBusy(
                            "Another worker is still processing this task DAG"
                        )
                    row.status = "running"
                    row.lease_owner = self.owner
                    row.lease_expires_at = self._lease_deadline()
                    row.completed_at = None
                    row.resume_count += 1
                    await db.commit()

                self.execution_id = row.id
                checkpoints = list(
                    await db.scalars(
                        select(TaskNodeCheckpoint)
                        .where(TaskNodeCheckpoint.execution_id == row.id)
                        .order_by(TaskNodeCheckpoint.created_at)
                    )
                )
                statuses: dict[str, TaskStatus] = {}
                results: dict[str, Any] = {}
                attempts: dict[str, int] = {}
                errors: dict[str, str] = {}
                for checkpoint in checkpoints:
                    status: TaskStatus = (
                        "pending" if checkpoint.status == "running" else checkpoint.status
                    )  # type: ignore[assignment]
                    checkpoint.status = status
                    statuses[checkpoint.task_id] = status
                    attempts[checkpoint.task_id] = checkpoint.attempts
                    if checkpoint.status == "completed":
                        results[checkpoint.task_id] = checkpoint.result
                    if checkpoint.error:
                        errors[checkpoint.task_id] = checkpoint.error
                await db.commit()
                return TaskCheckpointSnapshot(
                    execution_id=str(row.id),
                    resumed=not created,
                    results=results,
                    statuses=statuses,
                    attempts=attempts,
                    errors=errors,
                )
        raise TaskDAGExecutionBusy("Could not acquire the durable task execution")

    async def save(
        self,
        node: AgentTask,
        *,
        status: TaskStatus,
        attempts: int,
        result: Any = None,
        error: str = "",
    ) -> None:
        if self.execution_id is None:
            raise RuntimeError("Task checkpoint store has not been loaded")
        async with AsyncSessionLocal() as db:
            execution = await db.scalar(
                select(TaskExecution).where(TaskExecution.id == self.execution_id).with_for_update()
            )
            if execution is None or execution.lease_owner != self.owner:
                raise TaskDAGExecutionBusy("The durable task execution lease was lost")
            checkpoint = await db.scalar(
                select(TaskNodeCheckpoint)
                .where(
                    TaskNodeCheckpoint.execution_id == self.execution_id,
                    TaskNodeCheckpoint.task_id == node.id,
                )
                .with_for_update()
            )
            if checkpoint is None:
                raise RuntimeError(f"Task checkpoint does not exist: {node.id}")
            now = datetime.now(UTC)
            checkpoint.status = status
            checkpoint.attempts = attempts
            checkpoint.error = error or None
            if status == "running" and checkpoint.started_at is None:
                checkpoint.started_at = now
            if status == "completed":
                checkpoint.result = trace_value(result)
                checkpoint.completed_at = now
            elif status in ("failed", "blocked"):
                checkpoint.completed_at = now
            execution.lease_expires_at = self._lease_deadline()
            await db.commit()

    async def finish(self, status: Literal["completed", "failed", "interrupted"]) -> None:
        if self._finished or self.execution_id is None:
            return
        self._finished = True
        async with AsyncSessionLocal() as db:
            execution = await db.scalar(
                select(TaskExecution).where(TaskExecution.id == self.execution_id).with_for_update()
            )
            if execution is None or execution.lease_owner != self.owner:
                return
            execution.status = status
            execution.lease_owner = None
            execution.lease_expires_at = None
            execution.completed_at = datetime.now(UTC) if status != "interrupted" else None
            await db.commit()
