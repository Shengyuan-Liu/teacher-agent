import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.api.v1 import evaluations as evaluation_api
from app.core.database import AsyncSessionLocal
from app.evaluation.base import EvaluationCase
from app.evaluation.fixtures import STARTER_CASES
from app.evaluation.registry import get_suite, list_suites
from app.evaluation.runner import compare_summaries, execute_case, run_evaluation
from app.models import EvalResult


class FakeQueue:
    def __init__(self):
        self.jobs: list[tuple[str, str]] = []

    async def enqueue_job(self, name: str, run_id: str):
        self.jobs.append((name, run_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("suite_name", ["structured_output", "router_contract"])
async def test_starter_suites_pass_without_models(suite_name: str):
    suite = get_suite(suite_name)
    assert suite.info.requires_model is False

    for raw in STARTER_CASES[suite_name]:
        executed = await execute_case(
            suite_name=suite_name,
            case=EvaluationCase(
                key=raw["key"],
                input=raw["input"],
                expected=raw["expected"],
                tags=raw.get("tags", []),
            ),
            workspace_id=None,
            config={},
        )
        assert executed.error is None
        assert executed.outcome is not None
        assert executed.outcome.passed is True


def test_suite_registry_exposes_rag_adapter():
    names = {suite.name for suite in list_suites()}
    assert names == {
        "multi_agent_coordination",
        "rag_retrieval",
        "router_contract",
        "structured_output",
    }


@pytest.mark.asyncio
async def test_multi_agent_ablation_matrix_compares_quality_latency_and_cost():
    raw = STARTER_CASES["multi_agent_coordination"][2]
    outcomes = {}
    for variant in (
        "single_agent",
        "typed_dag",
        "sequential_dag",
        "no_synthesis",
    ):
        executed = await execute_case(
            suite_name="multi_agent_coordination",
            case=EvaluationCase(
                key=raw["key"],
                input=raw["input"],
                expected=raw["expected"],
                tags=raw["tags"],
            ),
            workspace_id=None,
            config={"execution_mode": "deterministic", "variant": variant},
        )
        assert executed.error is None
        assert executed.outcome is not None
        outcomes[variant] = executed.outcome

    assert outcomes["typed_dag"].scores["answer_quality"] == 1.0
    assert (
        outcomes["single_agent"].scores["claim_recall"]
        < outcomes["typed_dag"].scores["claim_recall"]
    )
    assert (
        outcomes["typed_dag"].output["critical_path_ms"]
        < outcomes["sequential_dag"].output["critical_path_ms"]
    )
    assert outcomes["no_synthesis"].scores["coherence"] < outcomes["typed_dag"].scores["coherence"]
    assert outcomes["single_agent"].output["agent_calls"] == 1
    assert outcomes["typed_dag"].output["agent_calls"] == 3
    assert outcomes["typed_dag"].output["dag"]["layers"] == [
        ["web_1", "qa_1"],
        ["answer_1"],
    ]


@pytest.mark.asyncio
async def test_live_multi_agent_benchmark_uses_real_tiers_and_usage_ledger(
    monkeypatch,
):
    from app.evaluation.suites import multi_agent_coordination as coordination

    raw = STARTER_CASES["multi_agent_coordination"][0]
    claims = raw["expected"]["claims"]

    class Reply:
        def __init__(self, text: str, model: str):
            self.text = text
            self.usage_metadata = {"input_tokens": 20, "output_tokens": 10}
            self.response_metadata = {"model_name": model}

    class Model:
        def __init__(self, tier):
            self.tier = tier

        async def ainvoke(self, messages):
            prompt = messages[-1].content
            if self.tier.value == "smart":
                return Reply(
                    f"{claims[0]} [W1] {claims[1]} [L1]",
                    "gpt-5.6-terra",
                )
            if "[WEB]" in prompt:
                return Reply(f"{claims[0]} [W1]", "gpt-5.6-luna")
            return Reply(f"{claims[1]} [L1]", "gpt-5.6-luna")

    monkeypatch.setattr(coordination, "chat_model", lambda tier: Model(tier))
    executed = await execute_case(
        suite_name="multi_agent_coordination",
        case=EvaluationCase(
            key=raw["key"],
            input=raw["input"],
            expected=raw["expected"],
            tags=raw["tags"],
        ),
        workspace_id=None,
        config={"execution_mode": "live", "variant": "typed_dag"},
    )

    assert executed.error is None
    assert executed.outcome is not None
    assert executed.outcome.passed is True
    assert [stage["tier"] for stage in executed.outcome.output["stages"]].count("fast") == 2
    assert [stage["tier"] for stage in executed.outcome.output["stages"]].count("smart") == 1
    assert executed.usage_payload["total_tokens"] == 90
    assert len(executed.usage_payload["calls"]) == 3


def test_baseline_comparison_only_gates_configured_regressions():
    comparison = compare_summaries(
        {"metrics": {"recall@5": 0.89, "mrr": 0.8}},
        {"metrics": {"recall@5": 0.94, "mrr": 0.81}},
        {"max_regression": {"recall@5": 0.02}},
    )

    assert comparison["metrics"]["recall@5"]["delta"] == -0.05
    assert comparison["metrics"]["recall@5"]["regressed"] is True
    assert comparison["metrics"]["mrr"]["regressed"] is False
    assert comparison["regressions"] == ["recall@5"]
    assert comparison["gate_passed"] is False


@pytest.mark.asyncio
async def test_eval_api_persists_case_results_and_baseline(auth_client: AsyncClient, monkeypatch):
    fake_queue = FakeQueue()

    async def queue():
        return fake_queue

    monkeypatch.setattr(evaluation_api, "get_queue", queue)
    workspace = (await auth_client.post("/workspaces", json={"name": "Evaluation"})).json()
    workspace_id = workspace["id"]

    suites = await auth_client.get(f"/workspaces/{workspace_id}/evals/suites")
    assert suites.status_code == 200
    assert {item["name"] for item in suites.json()} >= {
        "multi_agent_coordination",
        "router_contract",
        "structured_output",
    }

    created = await auth_client.post(
        f"/workspaces/{workspace_id}/evals/datasets/starter",
        json={"suite": "router_contract"},
    )
    assert created.status_code == 201
    dataset = created.json()
    assert dataset["case_count"] == len(STARTER_CASES["router_contract"])
    assert dataset["thresholds"]["min_scores"]["contract_accuracy"] == 1.0

    benchmark = await auth_client.post(
        f"/workspaces/{workspace_id}/evals/datasets/starter",
        json={"suite": "multi_agent_coordination"},
    )
    assert benchmark.status_code == 201
    assert benchmark.json()["default_config"]["execution_mode"] == "deterministic"
    assert benchmark.json()["thresholds"]["min_scores"]["answer_quality"] == 0.9

    queued = await auth_client.post(
        f"/workspaces/{workspace_id}/evals/datasets/{dataset['id']}/runs",
        json={"label": "baseline"},
    )
    assert queued.status_code == 202
    first_run = queued.json()
    assert fake_queue.jobs == [("run_evaluation_job", first_run["id"])]
    assert first_run["config"]["_runtime"]["fast"]["model"]
    assert first_run["config"]["_runtime"]["smart"]["model"]

    async with AsyncSessionLocal() as db:
        completed = await run_evaluation(db, uuid.UUID(first_run["id"]))
        assert completed.status == "completed"
        assert completed.summary["gate_passed"] is True
        assert completed.summary["pass_rate"] == 1.0
        result_count = await db.scalar(
            select(func.count(EvalResult.id)).where(EvalResult.run_id == completed.id)
        )
        completed.status = "pending"
        await db.commit()
        resumed = await run_evaluation(db, completed.id)
        resumed_count = await db.scalar(
            select(func.count(EvalResult.id)).where(EvalResult.run_id == completed.id)
        )
        assert resumed.status == "completed"
        assert resumed_count == result_count

    detail = await auth_client.get(f"/workspaces/{workspace_id}/evals/runs/{first_run['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert len(payload["results"]) == len(STARTER_CASES["router_contract"])
    assert all(result["passed"] for result in payload["results"])

    candidate = await auth_client.post(
        f"/workspaces/{workspace_id}/evals/datasets/{dataset['id']}/runs",
        json={"label": "candidate", "baseline_run_id": first_run["id"]},
    )
    assert candidate.status_code == 202
    second_run = candidate.json()
    async with AsyncSessionLocal() as db:
        completed = await run_evaluation(db, uuid.UUID(second_run["id"]))
        assert completed.comparison["baseline_run_id"] == first_run["id"]
        assert completed.comparison["regressions"] == []


@pytest.mark.asyncio
async def test_dataset_validation_and_ownership_scope(auth_client: AsyncClient):
    workspace = (await auth_client.post("/workspaces", json={"name": "Eval validation"})).json()
    workspace_id = workspace["id"]
    duplicate_keys = {
        "name": "Broken",
        "suite": "structured_output",
        "cases": [
            {"key": "same", "input": {}, "expected": {}},
            {"key": "same", "input": {}, "expected": {}},
        ],
    }
    response = await auth_client.post(
        f"/workspaces/{workspace_id}/evals/datasets", json=duplicate_keys
    )
    assert response.status_code == 422

    missing = await auth_client.get(f"/workspaces/{workspace_id}/evals/datasets/{uuid.uuid4()}")
    assert missing.status_code == 404
