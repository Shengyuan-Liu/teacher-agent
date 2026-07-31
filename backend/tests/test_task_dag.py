import asyncio
import uuid

import pytest
from httpx import AsyncClient

from app.agents.router import (
    AgentTask,
    filter_authorized_tasks,
    parse_decision,
)
from app.agents.task_dag import (
    TaskCheckpointSnapshot,
    TaskDAG,
    TaskDAGExecutor,
    TaskDAGValidationError,
)
from app.services.task_checkpoints import PostgresTaskCheckpointStore


class MemoryCheckpointStore:
    def __init__(self):
        self.execution_id = str(uuid.uuid4())
        self.loads = 0
        self.statuses = {}
        self.attempts = {}
        self.results = {}
        self.errors = {}
        self.finished = []

    async def load(self, dag):
        self.loads += 1
        for node in dag.nodes:
            self.statuses.setdefault(node.id, "pending")
        return TaskCheckpointSnapshot(
            execution_id=self.execution_id,
            resumed=self.loads > 1,
            statuses=dict(self.statuses),
            attempts=dict(self.attempts),
            results=dict(self.results),
            errors=dict(self.errors),
        )

    async def save(self, node, *, status, attempts, result=None, error=""):
        self.statuses[node.id] = status
        self.attempts[node.id] = attempts
        if status == "completed":
            self.results[node.id] = result
        if error:
            self.errors[node.id] = error
        else:
            self.errors.pop(node.id, None)

    async def finish(self, status):
        self.finished.append(status)


def test_legacy_multi_source_tasks_gain_a_synthesis_node():
    dag = TaskDAG.build(
        (
            AgentTask("web", "Find the biography"),
            AgentTask("qa", "Find the textbook theorems"),
        ),
        original_query="Compare the web and textbook evidence",
    )

    assert [[node.id for node in layer] for layer in dag.layers()] == [
        ["web_1", "qa_1"],
        ["answer_1"],
    ]
    assert dag.synthesis_node is not None
    assert dag.synthesis_node.depends_on == ("web_1", "qa_1")
    assert [node.agent for node in dag.worker_nodes] == ["web", "qa"]


def test_dag_rejects_cycles_unknown_dependencies_and_action_composition():
    with pytest.raises(TaskDAGValidationError, match="cycle"):
        TaskDAG(
            (
                AgentTask("qa", "a", id="a", depends_on=("b",)),
                AgentTask("web", "b", id="b", depends_on=("a",)),
            )
        ).validate()

    with pytest.raises(TaskDAGValidationError, match="unknown dependencies"):
        TaskDAG((AgentTask("qa", "a", id="a", depends_on=("missing",)),)).validate()

    with pytest.raises(TaskDAGValidationError, match="single-node"):
        TaskDAG.build(
            (AgentTask("quiz", "quiz me"), AgentTask("qa", "read this")),
            original_query="quiz me from this material",
        )


def test_router_accepts_explicit_typed_task_dag():
    decision = parse_decision(
        """
        {
          "intent": "web",
          "confidence": 0.98,
          "tasks": [
            {"id": "web_bio", "agent": "web", "query": "Find a biography", "depends_on": []},
            {"id": "local_work", "agent": "qa", "query": "Find related theorems", "depends_on": []},
            {"id": "final_answer", "agent": "answer", "query": "Answer the request",
             "depends_on": ["web_bio", "local_work"]}
          ],
          "alternatives": [],
          "reason": "two sources are required"
        }
        """
    )

    assert [task.id for task in decision.tasks] == [
        "web_bio",
        "local_work",
        "final_answer",
    ]
    assert decision.tasks[-1].depends_on == ("web_bio", "local_work")
    dag = TaskDAG.build(decision.tasks, original_query="original")
    assert [[node.id for node in layer] for layer in dag.layers()] == [
        ["web_bio", "local_work"],
        ["final_answer"],
    ]


def test_web_consent_prunes_synthesis_with_a_removed_dependency():
    tasks = (
        AgentTask("web", "web", id="web_source"),
        AgentTask("qa", "local", id="local_source"),
        AgentTask(
            "answer",
            "combine",
            id="answer",
            depends_on=("web_source", "local_source"),
        ),
    )
    authorized = filter_authorized_tasks(
        tasks,
        "Use my material",
        web_search_enabled=True,
    )
    assert [(task.id, task.agent) for task in authorized] == [("local_source", "qa")]


@pytest.mark.asyncio
async def test_executor_runs_same_layer_concurrently_and_shares_blackboard():
    dag = TaskDAG.build(
        (AgentTask("web", "web"), AgentTask("qa", "local")),
        original_query="combine",
    )
    both_started = asyncio.Event()
    started: list[str] = []

    async def gather(node, _blackboard):
        started.append(node.id)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.2)
        return {"source": node.id}

    async def answer(node, blackboard):
        return {
            "dependencies": [
                blackboard.result(dependency)["source"] for dependency in node.depends_on
            ]
        }

    executor = TaskDAGExecutor(
        dag,
        {"web": gather, "qa": gather, "answer": answer},
    )
    events = [event async for event in executor.run()]

    assert started == ["web_1", "qa_1"]
    assert executor.blackboard.statuses == {
        "web_1": "completed",
        "qa_1": "completed",
        "answer_1": "completed",
    }
    assert executor.blackboard.result("answer_1") == {"dependencies": ["web_1", "qa_1"]}
    assert [event.type for event in events].count("completed") == 3


@pytest.mark.asyncio
async def test_executor_retries_then_propagates_failure_to_dependants():
    dag = TaskDAG.build(
        (
            AgentTask("web", "web", max_attempts=2),
            AgentTask("qa", "local", max_attempts=1),
        ),
        original_query="combine",
    )
    calls = {"web": 0, "answer": 0}

    async def web(_node, _blackboard):
        calls["web"] += 1
        raise RuntimeError("provider unavailable")

    async def qa(_node, _blackboard):
        return {"source": "local"}

    async def answer(_node, _blackboard):
        calls["answer"] += 1
        return {}

    executor = TaskDAGExecutor(
        dag,
        {"web": web, "qa": qa, "answer": answer},
    )
    events = [event async for event in executor.run()]

    assert calls == {"web": 2, "answer": 0}
    assert executor.blackboard.statuses == {
        "web_1": "failed",
        "qa_1": "completed",
        "answer_1": "blocked",
    }
    failed = next(event for event in events if event.node.id == "web_1" and event.type == "failed")
    assert failed.attempts == 2
    assert "web_1" in executor.blackboard.errors["answer_1"]


@pytest.mark.asyncio
async def test_executor_enforces_per_node_timeout():
    dag = TaskDAG.build(
        (
            AgentTask(
                "qa",
                "slow lookup",
                timeout_seconds=0.01,
                max_attempts=1,
            ),
        ),
        original_query="slow lookup",
    )

    async def slow(_node, _blackboard):
        await asyncio.sleep(0.05)
        return {}

    executor = TaskDAGExecutor(dag, {"qa": slow})
    events = [event async for event in executor.run()]

    assert executor.blackboard.statuses["qa_1"] == "failed"
    assert events[-1].type == "failed"
    assert "TimeoutError" in events[-1].error


@pytest.mark.asyncio
async def test_executor_resumes_from_materialized_node_checkpoints():
    dag = TaskDAG.build(
        (AgentTask("web", "web"), AgentTask("qa", "local")),
        original_query="combine",
    )
    store = MemoryCheckpointStore()
    calls = {"web": 0, "qa": 0, "answer": 0}

    async def worker(node, _blackboard):
        calls[node.agent] += 1
        return {"source": node.agent}

    async def answer(node, blackboard):
        calls["answer"] += 1
        return {"sources": [blackboard.result(dep)["source"] for dep in node.depends_on]}

    first = TaskDAGExecutor(
        dag,
        {"web": worker, "qa": worker, "answer": answer},
        checkpoint_store=store,
    )
    stream = first.run()
    while True:
        event = await anext(stream)
        if event.node.id == "qa_1" and event.type == "completed":
            break
    await stream.aclose()

    assert calls == {"web": 1, "qa": 1, "answer": 0}
    assert store.finished == ["interrupted"]

    resumed = TaskDAGExecutor(
        dag,
        {"web": worker, "qa": worker, "answer": answer},
        checkpoint_store=store,
    )
    events = [event async for event in resumed.run()]

    assert calls == {"web": 1, "qa": 1, "answer": 1}
    assert [event.type for event in events[:2]] == ["restored", "restored"]
    assert resumed.blackboard.resumed is True
    assert resumed.blackboard.result("answer_1") == {"sources": ["web", "qa"]}
    assert store.finished[-1] == "completed"


def test_persisted_dag_payload_round_trips_retry_policy():
    dag = TaskDAG.build(
        (
            AgentTask(
                "web",
                "web",
                id="web_source",
                timeout_seconds=12,
                max_attempts=3,
            ),
            AgentTask("qa", "local", id="local_source"),
        ),
        original_query="combine",
    )

    restored = TaskDAG.from_payload(dag.as_payload())

    assert restored.nodes == dag.nodes


@pytest.mark.asyncio
async def test_postgres_checkpoint_store_materializes_results_and_enforces_lease(
    auth_client: AsyncClient,
):
    workspace = (
        await auth_client.post("/workspaces", json={"name": "Durable DAG integration"})
    ).json()
    session = (await auth_client.post(f"/workspaces/{workspace['id']}/chat/sessions")).json()
    dag = TaskDAG.build((AgentTask("qa", "local"),), original_query="local")
    execution_key = uuid.uuid4()
    first = PostgresTaskCheckpointStore(
        execution_key=execution_key,
        workspace_id=uuid.UUID(workspace["id"]),
        session_id=uuid.UUID(session["id"]),
    )
    snapshot = await first.load(dag)
    assert snapshot.resumed is False

    competing = PostgresTaskCheckpointStore(
        execution_key=execution_key,
        workspace_id=uuid.UUID(workspace["id"]),
        session_id=uuid.UUID(session["id"]),
    )
    with pytest.raises(RuntimeError, match="Another worker"):
        await competing.load(dag)

    await first.save(
        dag.nodes[0],
        status="completed",
        attempts=1,
        result={"source": "persisted"},
    )
    await first.finish("interrupted")

    resumed = PostgresTaskCheckpointStore(
        execution_key=execution_key,
        workspace_id=uuid.UUID(workspace["id"]),
        session_id=uuid.UUID(session["id"]),
    )
    restored = await resumed.load(dag)
    assert restored.resumed is True
    assert restored.statuses == {"qa_1": "completed"}
    assert restored.results == {"qa_1": {"source": "persisted"}}
    await resumed.finish("completed")
