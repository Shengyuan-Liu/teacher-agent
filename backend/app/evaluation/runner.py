"""Durable evaluation execution, aggregation and baseline regression gates."""

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.base import EvaluationCase, EvaluationContext, EvaluationOutcome
from app.evaluation.registry import get_suite
from app.models import EvalCase, EvalDataset, EvalResult, EvalRun
from app.services import usage


@dataclass(frozen=True)
class ExecutedCase:
    outcome: EvaluationOutcome | None
    latency_ms: float
    usage_payload: dict[str, Any]
    error: str | None = None


async def execute_case(
    *,
    suite_name: str,
    case: EvaluationCase,
    workspace_id: uuid.UUID | None,
    config: dict[str, Any],
) -> ExecutedCase:
    """Pure suite entry point shared by database runs, tests and the fast CLI."""
    suite = get_suite(suite_name)
    ledger = usage.start()
    started = perf_counter()
    try:
        outcome = await suite.evaluate(
            case, EvaluationContext(workspace_id=workspace_id, config=config)
        )
        return ExecutedCase(
            outcome=outcome,
            latency_ms=round((perf_counter() - started) * 1000, 3),
            usage_payload=ledger.as_payload(),
        )
    except Exception as exc:
        return ExecutedCase(
            outcome=None,
            latency_ms=round((perf_counter() - started) * 1000, 3),
            usage_payload=ledger.as_payload(),
            error=str(exc)[:4000],
        )


def summarize(results: list[EvalResult], thresholds: dict[str, Any]) -> dict[str, Any]:
    metric_values: dict[str, list[float]] = {}
    for result in results:
        if result.status != "completed":
            continue
        for name, value in result.scores.items():
            if isinstance(value, (int, float)):
                metric_values.setdefault(name, []).append(float(value))

    metrics = {
        name: round(sum(values) / len(values), 6)
        for name, values in sorted(metric_values.items())
        if values
    }
    min_scores = thresholds.get("min_scores", {})
    minimum_failures = {
        name: {"actual": metrics.get(name), "minimum": minimum}
        for name, minimum in min_scores.items()
        if metrics.get(name) is None or metrics[name] < float(minimum)
    }
    completed = [result for result in results if result.status == "completed"]
    costs = [result.cost_usd for result in results if result.cost_usd is not None]
    return {
        "cases": len(results),
        "completed": len(completed),
        "errors": sum(result.status == "error" for result in results),
        "passed": sum(result.passed is True for result in results),
        "failed": sum(result.passed is False for result in results),
        "pass_rate": round(sum(result.passed is True for result in results) / len(results), 6)
        if results
        else 0.0,
        "metrics": metrics,
        "latency_ms": round(sum(result.latency_ms or 0.0 for result in results), 3),
        "input_tokens": sum(result.input_tokens for result in results),
        "output_tokens": sum(result.output_tokens for result in results),
        "cost_usd": round(sum(costs), 6) if costs else None,
        "minimum_failures": minimum_failures,
        "gate_passed": not minimum_failures
        and all(result.status == "completed" for result in results),
    }


def compare_summaries(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    if not baseline:
        return {"baseline_run_id": None, "metrics": {}, "regressions": [], "gate_passed": True}

    current_metrics = current.get("metrics", {})
    baseline_metrics = baseline.get("metrics", {})
    allowed = thresholds.get("max_regression", {})
    comparisons: dict[str, dict[str, Any]] = {}
    regressions: list[str] = []
    for name in sorted(set(current_metrics) | set(baseline_metrics)):
        before = baseline_metrics.get(name)
        after = current_metrics.get(name)
        delta = (
            round(float(after) - float(before), 6)
            if before is not None and after is not None
            else None
        )
        limit = allowed.get(name)
        regressed = limit is not None and delta is not None and delta < -abs(float(limit))
        if regressed:
            regressions.append(name)
        comparisons[name] = {
            "baseline": before,
            "current": after,
            "delta": delta,
            "max_regression": limit,
            "regressed": regressed,
        }
    return {
        "metrics": comparisons,
        "regressions": regressions,
        "gate_passed": not regressions,
    }


async def run_evaluation(db: AsyncSession, run_id: uuid.UUID) -> EvalRun:
    """Execute one persisted run and commit each case as a durable checkpoint."""
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise ValueError(f"Evaluation run not found: {run_id}")
    if run.status not in ("pending", "failed"):
        return run

    run.status = "running"
    run.started_at = datetime.now(UTC)
    run.completed_at = None
    run.error = None
    await db.commit()

    try:
        cases = list(
            await db.scalars(
                select(EvalCase)
                .where(EvalCase.dataset_id == run.dataset_id, EvalCase.enabled.is_(True))
                .order_by(EvalCase.position)
            )
        )
        dataset = await db.get(EvalDataset, run.dataset_id)
        if dataset is None:
            raise ValueError("Evaluation dataset was deleted")
        thresholds = dataset.thresholds or {}
        config = {**(dataset.default_config or {}), **(run.config or {})}
        if run.variant:
            config["variant"] = run.variant

        completed_case_ids = set(
            await db.scalars(select(EvalResult.case_id).where(EvalResult.run_id == run.id))
        )
        for row in cases:
            if row.id in completed_case_ids:
                continue
            executed = await execute_case(
                suite_name=run.suite,
                case=EvaluationCase(
                    key=row.key,
                    input=row.input_json,
                    expected=row.expected_json,
                    tags=row.tags,
                    metadata=row.metadata_json,
                ),
                workspace_id=run.workspace_id,
                config=config,
            )
            payload = executed.usage_payload
            outcome = executed.outcome
            result = EvalResult(
                run_id=run.id,
                case_id=row.id,
                case_key=row.key,
                status="completed" if outcome is not None else "error",
                passed=outcome.passed if outcome is not None else False,
                output=outcome.output if outcome is not None else {},
                scores=outcome.scores if outcome is not None else {},
                details={
                    **(outcome.details if outcome is not None else {}),
                    "usage_calls": payload.get("calls", []),
                },
                latency_ms=executed.latency_ms,
                input_tokens=int(payload.get("input_tokens", 0)),
                output_tokens=int(payload.get("output_tokens", 0)),
                cost_usd=payload.get("cost_usd"),
                error=executed.error,
            )
            db.add(result)
            await db.commit()

        results = list(
            await db.scalars(
                select(EvalResult)
                .where(EvalResult.run_id == run.id)
                .order_by(EvalResult.created_at)
            )
        )
        run.summary = summarize(results, thresholds)
        baseline = await db.get(EvalRun, run.baseline_run_id) if run.baseline_run_id else None
        run.comparison = compare_summaries(
            run.summary, baseline.summary if baseline else None, thresholds
        )
        run.comparison["baseline_run_id"] = str(baseline.id) if baseline else None
        run.summary["gate_passed"] = bool(
            run.summary["gate_passed"] and run.comparison["gate_passed"]
        )
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        await db.commit()
        return run
    except Exception as exc:
        await db.rollback()
        run = await db.get(EvalRun, run_id)
        if run is None:
            raise
        run.status = "failed"
        run.error = str(exc)[:4000]
        run.completed_at = datetime.now(UTC)
        await db.commit()
        return run


def git_sha() -> str | None:
    value = os.getenv("GIT_SHA") or os.getenv("GITHUB_SHA")
    return value[:64] if value else None
