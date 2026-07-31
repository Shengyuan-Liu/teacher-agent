"""Provider-agnostic execution semantics for Router-produced task graphs.

The graph is immutable once a turn starts: retries must execute the same node
ids, dependencies and queries that were originally persisted. Nodes in one
topological layer run concurrently, while each layer is a barrier for the next.
Only completed dependency results cross that barrier through the blackboard;
failed dependencies block downstream work instead of providing partial input.

Persistence is injected through ``TaskCheckpointStore`` so this module remains
usable in deterministic evaluations without PostgreSQL.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

TaskAgent = Literal[
    "qa",
    "web",
    "quiz",
    "test",
    "review",
    "progress",
    "plan",
    "explain",
    "lecture",
    "answer",
]
TaskKind = Literal["knowledge", "action", "synthesis"]
TaskStatus = Literal["pending", "running", "completed", "failed", "blocked"]
TaskEventType = Literal["started", "restored", "completed", "failed", "blocked"]

KNOWLEDGE_AGENTS = frozenset({"qa", "web"})
SYNTHESIS_AGENTS = frozenset({"answer"})


class TaskDAGValidationError(ValueError):
    """Raised when a task graph is structurally or semantically invalid."""


class TaskDAGExecutionBusy(RuntimeError):
    """Raised when another worker still owns the durable execution lease."""


@dataclass(frozen=True)
class AgentTask:
    """A typed unit of work in an agent execution graph.

    ``agent`` and ``query`` remain the first fields so legacy two-argument
    construction continues to work.
    """

    agent: TaskAgent
    query: str
    id: str = ""
    depends_on: tuple[str, ...] = ()
    kind: TaskKind | None = None
    timeout_seconds: float | None = None
    max_attempts: int | None = None

    @property
    def resolved_kind(self) -> TaskKind:
        if self.kind is not None:
            return self.kind
        if self.agent in KNOWLEDGE_AGENTS:
            return "knowledge"
        if self.agent in SYNTHESIS_AGENTS:
            return "synthesis"
        return "action"


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "task"


@dataclass(frozen=True)
class TaskDAG:
    """Normalized graph; ``build`` is the only place that adds implicit synthesis."""

    nodes: tuple[AgentTask, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TaskDAG:
        """Rebuild the exact graph persisted before a process interruption."""

        raw_nodes = payload.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise TaskDAGValidationError("Persisted task DAG has no nodes")
        nodes = []
        for raw in raw_nodes:
            if not isinstance(raw, Mapping):
                raise TaskDAGValidationError("Persisted task DAG node must be an object")
            nodes.append(
                AgentTask(
                    id=str(raw.get("id") or ""),
                    agent=str(raw.get("agent") or "qa"),  # type: ignore[arg-type]
                    kind=str(raw.get("kind") or "knowledge"),  # type: ignore[arg-type]
                    query=str(raw.get("query") or ""),
                    depends_on=tuple(str(item) for item in raw.get("depends_on") or ()),
                    timeout_seconds=float(raw["timeout_seconds"])
                    if raw.get("timeout_seconds") is not None
                    else None,
                    max_attempts=int(raw["max_attempts"])
                    if raw.get("max_attempts") is not None
                    else None,
                )
            )
        dag = cls(tuple(nodes))
        dag.validate()
        return dag

    @classmethod
    def build(
        cls,
        tasks: Sequence[AgentTask],
        *,
        original_query: str,
        max_nodes: int = 8,
        default_timeout_seconds: float = 90,
        default_max_attempts: int = 2,
    ) -> TaskDAG:
        if not tasks:
            raise TaskDAGValidationError("A task DAG must contain at least one task")

        counters: dict[str, int] = {}
        normalized: list[AgentTask] = []
        used_ids: set[str] = set()
        for task in tasks:
            if task.id:
                task_id = _slug(task.id)
            else:
                base = _slug(task.agent)
                counters[base] = counters.get(base, 0) + 1
                task_id = f"{base}_{counters[base]}"
            if task_id in used_ids:
                raise TaskDAGValidationError(f"Duplicate task id: {task_id}")
            used_ids.add(task_id)

            normalized.append(
                replace(
                    task,
                    id=task_id,
                    depends_on=tuple(_slug(dep) for dep in task.depends_on),
                    kind=task.resolved_kind,
                    timeout_seconds=task.timeout_seconds or default_timeout_seconds,
                    max_attempts=max(1, task.max_attempts or default_max_attempts),
                )
            )

        knowledge = [node for node in normalized if node.resolved_kind == "knowledge"]
        actions = [node for node in normalized if node.resolved_kind == "action"]
        synthesis = [node for node in normalized if node.resolved_kind == "synthesis"]

        if actions and len(normalized) > 1:
            raise TaskDAGValidationError("Action agents must execute as a single-node DAG")
        if len(synthesis) > 1:
            raise TaskDAGValidationError("A task DAG can contain only one synthesis node")

        if synthesis:
            answer = synthesis[0]
            if not answer.depends_on:
                answer = replace(answer, depends_on=tuple(node.id for node in knowledge))
                normalized[normalized.index(synthesis[0])] = answer
        elif len(knowledge) > 1:
            normalized.append(
                AgentTask(
                    agent="answer",
                    query=original_query,
                    id="answer_1",
                    depends_on=tuple(node.id for node in knowledge),
                    kind="synthesis",
                    timeout_seconds=default_timeout_seconds,
                    max_attempts=max(1, default_max_attempts),
                )
            )

        dag = cls(tuple(normalized))
        dag.validate(max_nodes=max_nodes)
        return dag

    @property
    def worker_nodes(self) -> tuple[AgentTask, ...]:
        return tuple(node for node in self.nodes if node.resolved_kind != "synthesis")

    @property
    def synthesis_node(self) -> AgentTask | None:
        return next(
            (node for node in self.nodes if node.resolved_kind == "synthesis"),
            None,
        )

    def node(self, task_id: str) -> AgentTask:
        for node in self.nodes:
            if node.id == task_id:
                return node
        raise KeyError(task_id)

    def validate(self, *, max_nodes: int = 8) -> None:
        if not self.nodes:
            raise TaskDAGValidationError("A task DAG must contain at least one node")
        if len(self.nodes) > max_nodes:
            raise TaskDAGValidationError(
                f"Task DAG contains {len(self.nodes)} nodes; maximum is {max_nodes}"
            )

        ids = [node.id for node in self.nodes]
        if any(not task_id for task_id in ids):
            raise TaskDAGValidationError("Every task DAG node must have an id")
        if len(ids) != len(set(ids)):
            raise TaskDAGValidationError("Task DAG node ids must be unique")

        known_ids = set(ids)
        for node in self.nodes:
            if node.id in node.depends_on:
                raise TaskDAGValidationError(f"Task {node.id} cannot depend on itself")
            missing = set(node.depends_on) - known_ids
            if missing:
                raise TaskDAGValidationError(
                    f"Task {node.id} has unknown dependencies: " + ", ".join(sorted(missing))
                )
            if node.resolved_kind == "synthesis":
                if not node.depends_on:
                    raise TaskDAGValidationError(f"Synthesis task {node.id} requires dependencies")
                if any(self.node(dep).resolved_kind != "knowledge" for dep in node.depends_on):
                    raise TaskDAGValidationError(
                        f"Synthesis task {node.id} can only depend on knowledge tasks"
                    )

        self.layers()

    def layers(self) -> tuple[tuple[AgentTask, ...], ...]:
        remaining = {node.id: set(node.depends_on) for node in self.nodes}
        ordered_ids = [node.id for node in self.nodes]
        layers: list[tuple[AgentTask, ...]] = []
        resolved: set[str] = set()
        while remaining:
            ready_ids = [
                task_id
                for task_id in ordered_ids
                if task_id in remaining and remaining[task_id] <= resolved
            ]
            if not ready_ids:
                raise TaskDAGValidationError(
                    "Task DAG contains a dependency cycle involving: "
                    + ", ".join(task_id for task_id in ordered_ids if task_id in remaining)
                )
            layers.append(tuple(self.node(task_id) for task_id in ready_ids))
            resolved.update(ready_ids)
            for task_id in ready_ids:
                remaining.pop(task_id)
        return tuple(layers)

    def as_payload(
        self,
        *,
        statuses: Mapping[str, TaskStatus] | None = None,
        attempts: Mapping[str, int] | None = None,
        errors: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        statuses = statuses or {}
        attempts = attempts or {}
        errors = errors or {}
        return {
            "type": "task_dag",
            "layers": [[node.id for node in layer] for layer in self.layers()],
            "nodes": [
                {
                    "id": node.id,
                    "agent": node.agent,
                    "kind": node.resolved_kind,
                    "query": node.query,
                    "depends_on": list(node.depends_on),
                    "timeout_seconds": node.timeout_seconds,
                    "max_attempts": node.max_attempts,
                    "status": statuses.get(node.id, "pending"),
                    "attempts": attempts.get(node.id, 0),
                    **({"error": errors[node.id]} if node.id in errors else {}),
                }
                for node in self.nodes
            ],
        }


@dataclass
class TaskBlackboard:
    """Materialized node state shared with handlers and durable checkpoints.

    A handler should read only results named in its node's ``depends_on``. The
    executor establishes that ordering; the blackboard intentionally stays a
    simple mapping so it can be serialized and inspected without framework state.
    """

    results: dict[str, Any] = field(default_factory=dict)
    statuses: dict[str, TaskStatus] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    execution_id: str | None = None
    resumed: bool = False

    def result(self, task_id: str) -> Any:
        return self.results[task_id]


@dataclass(frozen=True)
class TaskEvent:
    """Observable state transition emitted exactly once per executor transition."""

    type: TaskEventType
    node: AgentTask
    status: TaskStatus
    attempts: int = 0
    result: Any = None
    error: str = ""


TaskHandler = Callable[[AgentTask, TaskBlackboard], Awaitable[Any]]


@dataclass(frozen=True)
class TaskCheckpointSnapshot:
    """Serializable state restored before a durable DAG continues."""

    execution_id: str
    resumed: bool
    results: dict[str, Any] = field(default_factory=dict)
    statuses: dict[str, TaskStatus] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class TaskCheckpointStore(Protocol):
    """Persistence boundary kept outside the orchestration package."""

    async def load(self, dag: TaskDAG) -> TaskCheckpointSnapshot: ...

    async def save(
        self,
        node: AgentTask,
        *,
        status: TaskStatus,
        attempts: int,
        result: Any = None,
        error: str = "",
    ) -> None: ...

    async def finish(self, status: Literal["completed", "failed", "interrupted"]) -> None: ...


class TaskDAGExecutor:
    """Execute each topological layer concurrently and propagate failures.

    Node retries are local to a node. A failed node does not cancel independent
    siblings in the same layer; their completed results remain reusable if the
    durable execution is resumed later.
    """

    def __init__(
        self,
        dag: TaskDAG,
        handlers: Mapping[str, TaskHandler],
        *,
        default_timeout_seconds: float = 90,
        default_max_attempts: int = 2,
        checkpoint_store: TaskCheckpointStore | None = None,
    ) -> None:
        self.dag = dag
        self.handlers = handlers
        self.default_timeout_seconds = default_timeout_seconds
        self.default_max_attempts = max(1, default_max_attempts)
        self.checkpoint_store = checkpoint_store
        self.blackboard = TaskBlackboard(statuses={node.id: "pending" for node in dag.nodes})

    async def _run_node(self, node: AgentTask) -> tuple[bool, Any, str, int]:
        handler = self.handlers.get(node.agent)
        if handler is None:
            return False, None, f"No handler registered for agent '{node.agent}'", 0

        max_attempts = max(1, node.max_attempts or self.default_max_attempts)
        timeout = node.timeout_seconds or self.default_timeout_seconds
        last_error = ""
        prior_attempts = self.blackboard.attempts.get(node.id, 0)
        if prior_attempts >= max_attempts:
            return (
                False,
                None,
                self.blackboard.errors.get(node.id, "Maximum attempts already exhausted"),
                prior_attempts,
            )
        for attempt in range(prior_attempts + 1, max_attempts + 1):
            self.blackboard.attempts[node.id] = attempt
            if self.checkpoint_store is not None:
                await self.checkpoint_store.save(
                    node,
                    status="running",
                    attempts=attempt,
                    error=self.blackboard.errors.get(node.id, ""),
                )
            try:
                result = await asyncio.wait_for(
                    handler(node, self.blackboard),
                    timeout=timeout,
                )
                return True, result, "", attempt
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - failures become graph state
                last_error = f"{type(exc).__name__}: {exc}"
                self.blackboard.errors[node.id] = last_error
                if self.checkpoint_store is not None:
                    await self.checkpoint_store.save(
                        node,
                        status="running",
                        attempts=attempt,
                        error=last_error,
                    )
        return False, None, last_error, max_attempts

    async def run(self) -> AsyncIterator[TaskEvent]:
        terminal_status: Literal["completed", "failed", "interrupted"] = "interrupted"
        if self.checkpoint_store is not None:
            snapshot = await self.checkpoint_store.load(self.dag)
            self.blackboard.execution_id = snapshot.execution_id
            self.blackboard.resumed = snapshot.resumed
            self.blackboard.results.update(snapshot.results)
            self.blackboard.statuses.update(snapshot.statuses)
            self.blackboard.attempts.update(snapshot.attempts)
            self.blackboard.errors.update(snapshot.errors)
        try:
            for layer in self.dag.layers():
                runnable: list[AgentTask] = []
                for node in layer:
                    if self.blackboard.statuses.get(node.id) == "completed":
                        yield TaskEvent(
                            type="restored",
                            node=node,
                            status="completed",
                            attempts=self.blackboard.attempts.get(node.id, 0),
                            result=self.blackboard.results.get(node.id),
                        )
                        continue

                    failed_dependencies = [
                        dep
                        for dep in node.depends_on
                        if self.blackboard.statuses.get(dep) != "completed"
                    ]
                    if failed_dependencies:
                        error = "Blocked by failed dependencies: " + ", ".join(failed_dependencies)
                        self.blackboard.statuses[node.id] = "blocked"
                        self.blackboard.errors[node.id] = error
                        if self.checkpoint_store is not None:
                            await self.checkpoint_store.save(
                                node,
                                status="blocked",
                                attempts=self.blackboard.attempts.get(node.id, 0),
                                error=error,
                            )
                        yield TaskEvent(
                            type="blocked",
                            node=node,
                            status="blocked",
                            error=error,
                        )
                        continue

                    self.blackboard.statuses[node.id] = "running"
                    runnable.append(node)
                    yield TaskEvent(
                        type="started",
                        node=node,
                        status="running",
                        attempts=self.blackboard.attempts.get(node.id, 0),
                    )

                # Treat the layer as a barrier. Waiting for every sibling preserves
                # independent successes even when another branch exhausts retries.
                outcomes = await asyncio.gather(*(self._run_node(node) for node in runnable))
                for node, (succeeded, result, error, attempts) in zip(
                    runnable, outcomes, strict=True
                ):
                    if succeeded:
                        self.blackboard.statuses[node.id] = "completed"
                        self.blackboard.results[node.id] = result
                        self.blackboard.errors.pop(node.id, None)
                        if self.checkpoint_store is not None:
                            await self.checkpoint_store.save(
                                node,
                                status="completed",
                                attempts=attempts,
                                result=result,
                            )
                        yield TaskEvent(
                            type="completed",
                            node=node,
                            status="completed",
                            attempts=attempts,
                            result=result,
                        )
                    else:
                        self.blackboard.statuses[node.id] = "failed"
                        self.blackboard.errors[node.id] = error
                        if self.checkpoint_store is not None:
                            await self.checkpoint_store.save(
                                node,
                                status="failed",
                                attempts=attempts,
                                error=error,
                            )
                        yield TaskEvent(
                            type="failed",
                            node=node,
                            status="failed",
                            attempts=attempts,
                            error=error,
                        )
            terminal_status = (
                "completed"
                if all(status == "completed" for status in self.blackboard.statuses.values())
                else "failed"
            )
        finally:
            # Cancellation and process-level failures leave the execution resumable;
            # completed/failed are used only after all graph transitions settle.
            if self.checkpoint_store is not None:
                await self.checkpoint_store.finish(terminal_status)
