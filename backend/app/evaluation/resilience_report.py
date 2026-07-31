"""Model-free load and fault profile for orchestration and governance controls."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.agents.task_dag import AgentTask, TaskBlackboard, TaskDAG, TaskDAGExecutor
from app.evaluation.benchmark_report import distribution
from app.services import resource_governance as governance
from app.services.resource_governance import (
    BudgetLimits,
    CircuitBreaker,
    CircuitOpenError,
    ResourceBudgetExceeded,
    ResourceCache,
    ResourceLedger,
)

REPORT_SCHEMA_VERSION = "1.0.0"


@dataclass
class MemoryRedis:
    values: dict[str, str] = field(default_factory=dict)

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int):
        self.values[key] = value

    async def aclose(self):
        return None


async def _dag_turn(turn_id: int, failure: str) -> dict[str, Any]:
    dag = TaskDAG.build(
        (
            AgentTask("web", "web", max_attempts=2),
            AgentTask("qa", "local", max_attempts=2),
        ),
        original_query="combine",
        default_timeout_seconds=0.1,
    )
    calls = {"web": 0, "qa": 0, "answer": 0}

    async def worker(node: AgentTask, _blackboard: TaskBlackboard):
        calls[node.agent] += 1
        await asyncio.sleep(0.001 + (turn_id % 3) * 0.0002)
        if node.agent == "web" and failure == "transient" and calls["web"] == 1:
            raise TimeoutError("injected transient provider timeout")
        if node.agent == "web" and failure == "permanent":
            raise TimeoutError("injected permanent provider timeout")
        return {"agent": node.agent, "turn": turn_id}

    async def answer(node: AgentTask, blackboard: TaskBlackboard):
        calls["answer"] += 1
        await asyncio.sleep(0.0005)
        return [blackboard.result(dep)["agent"] for dep in node.depends_on]

    executor = TaskDAGExecutor(
        dag,
        {"web": worker, "qa": worker, "answer": answer},
        default_max_attempts=2,
    )
    started = perf_counter()
    events = [event async for event in executor.run()]
    latency_ms = (perf_counter() - started) * 1000
    return {
        "latency_ms": latency_ms,
        "completed": executor.blackboard.statuses.get("answer_1") == "completed",
        "blocked": executor.blackboard.statuses.get("answer_1") == "blocked",
        "calls": calls,
        "events": [event.type for event in events],
    }


async def _load_scenario(
    *,
    turns: int,
    concurrency: int,
    failure: str,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(turn_id: int):
        async with semaphore:
            return await _dag_turn(turn_id, failure)

    started = perf_counter()
    results = await asyncio.gather(*(guarded(turn_id) for turn_id in range(turns)))
    wall_seconds = perf_counter() - started
    completed = sum(item["completed"] for item in results)
    blocked = sum(item["blocked"] for item in results)
    retries = sum(max(0, item["calls"]["web"] - 1) for item in results)
    return {
        "turns": turns,
        "concurrency": concurrency,
        "completed": completed,
        "blocked": blocked,
        "success_rate": round(completed / turns, 6),
        "retry_attempts": retries,
        "throughput_turns_per_second": round(turns / wall_seconds, 3),
        "latency_ms": distribution([item["latency_ms"] for item in results]),
    }


def _budget_contention() -> dict[str, Any]:
    allowed_calls = 12
    requested_calls = 50
    ledger = ResourceLedger(
        limits=BudgetLimits(
            max_model_calls=allowed_calls,
            max_tokens=1_000_000,
            max_cost_usd=10,
            soft_ratio=0.8,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
    )
    admitted = 0
    blocked = 0
    for _call in range(requested_calls):
        try:
            ledger.reserve_model("fast", lambda _tier: "fast-model", lambda _model: (0.1, 0.2))
            admitted += 1
        except ResourceBudgetExceeded:
            blocked += 1
    return {
        "requested": requested_calls,
        "admitted": admitted,
        "blocked": blocked,
        "limit": allowed_calls,
        "oversubscribed": admitted > allowed_calls,
        "hard_stop": ledger.hard_stop,
    }


async def _cache_stampede() -> dict[str, Any]:
    redis = MemoryRedis()
    cache = ResourceCache(redis_factory=lambda: redis)
    workspace_id = uuid.uuid4()
    computes = 0
    previous_unavailable = governance._redis_unavailable_until
    governance._redis_unavailable_until = 0

    async def compute():
        nonlocal computes
        computes += 1
        await asyncio.sleep(0.005)
        return {"intent": "qa"}

    try:
        results = await asyncio.gather(
            *(
                cache.get_or_compute(
                    namespace="resilience",
                    workspace_id=workspace_id,
                    key_payload={"query_hash": "fixed"},
                    ttl_seconds=60,
                    compute=compute,
                )
                for _request in range(50)
            )
        )
    finally:
        governance._redis_unavailable_until = previous_unavailable
    return {
        "requests": len(results),
        "computations": computes,
        "coalesced": len(results) - computes,
        "single_flight_effective": computes == 1,
        "consistent": all(result == {"intent": "qa"} for result in results),
    }


async def _circuit_recovery() -> dict[str, Any]:
    now = [100.0]
    breaker = CircuitBreaker(
        use_redis=False,
        clock=lambda: now[0],
        failure_threshold=3,
        failure_window_seconds=60,
        recovery_seconds=10,
        half_open_timeout_seconds=5,
    )

    async def fail():
        raise TimeoutError("injected")

    for _failure in range(3):
        try:
            await breaker.call("resilience:provider", fail)
        except TimeoutError:
            pass
    blocked = 0
    for _request in range(50):
        try:
            await breaker.before_call("resilience:provider")
        except CircuitOpenError:
            blocked += 1
    now[0] += 11

    async def probe():
        try:
            return await breaker.before_call("resilience:provider")
        except CircuitOpenError:
            return "blocked"

    probes = await asyncio.gather(probe(), probe())
    await breaker.record_success("resilience:provider")
    closed = await breaker.before_call("resilience:provider")
    return {
        "blocked_while_open": blocked,
        "half_open_admitted": probes.count("half_open"),
        "half_open_blocked": probes.count("blocked"),
        "state_after_success": closed,
    }


async def run_resilience_profile(
    *,
    turns: int = 200,
    concurrency: int = 50,
) -> dict[str, Any]:
    if turns < 1 or concurrency < 1:
        raise ValueError("turns and concurrency must be positive")
    healthy, transient, permanent = await asyncio.gather(
        _load_scenario(turns=turns, concurrency=concurrency, failure="none"),
        _load_scenario(turns=turns, concurrency=concurrency, failure="transient"),
        _load_scenario(turns=turns, concurrency=concurrency, failure="permanent"),
    )
    cache, circuit = await asyncio.gather(_cache_stampede(), _circuit_recovery())
    budget = _budget_contention()
    gates = {
        "healthy_success_rate": healthy["success_rate"] == 1,
        "transient_retry_recovery": transient["success_rate"] == 1
        and transient["retry_attempts"] == turns,
        "permanent_failure_propagation": permanent["blocked"] == turns,
        "budget_no_oversubscription": not budget["oversubscribed"] and budget["hard_stop"],
        "cache_single_flight": cache["single_flight_effective"] and cache["consistent"],
        "circuit_half_open_exclusive": circuit["half_open_admitted"] == 1
        and circuit["half_open_blocked"] == 1
        and circuit["state_after_success"] == "closed",
    }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "profile": {"turns": turns, "concurrency": concurrency, "model_calls": False},
        "scenarios": {
            "healthy": healthy,
            "transient_timeout": transient,
            "permanent_timeout": permanent,
            "budget_contention": budget,
            "cache_stampede": cache,
            "circuit_recovery": circuit,
        },
        "gates": gates,
        "passed": all(gates.values()),
        "limitations": [
            "This profile exercises real orchestration and governance code without model calls.",
            (
                "Run the separate HTTP load command against a deployed stack for network "
                "and database SLOs."
            ),
        ],
    }


def render_resilience_markdown(report: dict[str, Any]) -> str:
    scenarios = report["scenarios"]
    lines = [
        "# Agent Resilience and Load Profile",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Turns per DAG scenario: `{report['profile']['turns']}`",
        f"- Concurrency: `{report['profile']['concurrency']}`",
        f"- Overall gate: `{'PASS' if report['passed'] else 'FAIL'}`",
        "",
        "## DAG load and failures",
        "",
        "| Scenario | Success | Blocked | Retries | Throughput turns/s | P50/P95 ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key in ("healthy", "transient_timeout", "permanent_timeout"):
        item = scenarios[key]
        lines.append(
            f"| `{key}` | {item['success_rate']:.1%} | {item['blocked']} | "
            f"{item['retry_attempts']} | {item['throughput_turns_per_second']:.1f} | "
            f"{item['latency_ms']['p50']:.2f}/{item['latency_ms']['p95']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Governance fault injection",
            "",
            f"- Budget contention: {scenarios['budget_contention']['admitted']} admitted, "
            f"{scenarios['budget_contention']['blocked']} blocked, no oversubscription.",
            f"- Cache stampede: {scenarios['cache_stampede']['requests']} requests produced "
            f"{scenarios['cache_stampede']['computations']} computation.",
            f"- Circuit: {scenarios['circuit_recovery']['blocked_while_open']} calls blocked "
            "while open; exactly one half-open probe admitted; success closed the circuit.",
            "",
            "## Gates",
            "",
        ]
    )
    lines.extend(
        f"- [{'x' if passed else ' '}] `{name}`" for name, passed in report["gates"].items()
    )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            *[f"- {item}" for item in report["limitations"]],
            "",
        ]
    )
    return "\n".join(lines)


def write_resilience_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "agent-resilience.json"
    markdown_path = output_dir / "agent-resilience.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_resilience_markdown(report), encoding="utf-8")
    return json_path, markdown_path
