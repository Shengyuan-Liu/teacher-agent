import asyncio

import pytest

from app.agents.router import (
    AgentTask,
    filter_authorized_tasks,
    parse_decision,
)
from app.agents.task_dag import (
    TaskDAG,
    TaskDAGExecutor,
    TaskDAGValidationError,
)


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
