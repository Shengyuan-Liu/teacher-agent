"""Model-free contracts for budgets, tenant cache keys and circuit recovery."""

from __future__ import annotations

import uuid

from app.evaluation.base import EvaluationCase, EvaluationContext, EvaluationOutcome, SuiteInfo
from app.evaluation.registry import register
from app.services.resource_governance import (
    BudgetLimits,
    CircuitBreaker,
    CircuitOpenError,
    ResourceBudgetExceeded,
    ResourceLedger,
    cache_key,
)


def _model(tier: str) -> str:
    return f"{tier}-model"


def _prices(model: str) -> tuple[float, float] | None:
    return {
        "fast-model": (0.1, 0.2),
        "smart-model": (10.0, 20.0),
    }.get(model)


class ResourceGovernanceSuite:
    info = SuiteInfo(
        name="resource_governance",
        description=(
            "Fault-injects per-turn budget reservations, workspace cache isolation "
            "and closed/open/half-open circuit transitions without calling a model."
        ),
        metrics=(
            "resource_governance_accuracy",
            "budget_enforcement",
            "cache_isolation",
            "circuit_recovery",
        ),
    )

    async def evaluate(self, case: EvaluationCase, context: EvaluationContext) -> EvaluationOutcome:
        del context
        surface = str(case.input.get("surface") or "")
        if surface == "budget":
            output = self._budget(case)
            metric = "budget_enforcement"
        elif surface == "cache":
            output = self._cache(case)
            metric = "cache_isolation"
        elif surface == "circuit":
            output = await self._circuit(case)
            metric = "circuit_recovery"
        else:
            raise ValueError("resource_governance surface must be budget, cache or circuit")

        matched = all(output.get(key) == value for key, value in case.expected.items())
        return EvaluationOutcome(
            passed=matched,
            output=output,
            scores={
                "resource_governance_accuracy": float(matched),
                metric: float(matched),
            },
            details={"expected": case.expected},
        )

    @staticmethod
    def _budget(case: EvaluationCase) -> dict:
        limits = BudgetLimits(
            max_model_calls=int(case.input.get("max_model_calls", 4)),
            max_tokens=int(case.input.get("max_tokens", 100_000)),
            max_cost_usd=float(case.input.get("max_cost_usd", 1.0)),
            soft_ratio=float(case.input.get("soft_ratio", 0.8)),
            estimated_input_tokens=int(case.input.get("estimated_input_tokens", 1_000)),
            estimated_output_tokens=int(case.input.get("estimated_output_tokens", 1_000)),
        )
        ledger = ResourceLedger(limits=limits)
        selected: list[str] = []
        blocked = False
        for tier in case.input.get("calls", []):
            try:
                selected.append(ledger.reserve_model(str(tier), _model, _prices))
            except ResourceBudgetExceeded:
                blocked = True
                break
        return {
            "selected_tiers": selected,
            "blocked": blocked,
            "downgraded_calls": ledger.downgrade_count,
            "hard_stop": ledger.hard_stop,
        }

    @staticmethod
    def _cache(case: EvaluationCase) -> dict:
        first = uuid.UUID(str(case.input["workspace_a"]))
        second = uuid.UUID(str(case.input["workspace_b"]))
        payload = case.input.get("payload", {})
        first_key = cache_key("evaluation", first, payload)
        return {
            "stable": first_key == cache_key("evaluation", first, payload),
            "workspace_isolated": first_key != cache_key("evaluation", second, payload),
            "content_hidden": all(str(value) not in first_key for value in payload.values()),
        }

    @staticmethod
    async def _circuit(case: EvaluationCase) -> dict:
        now = [100.0]
        breaker = CircuitBreaker(
            use_redis=False,
            clock=lambda: now[0],
            failure_threshold=int(case.input.get("failure_threshold", 2)),
            failure_window_seconds=60,
            recovery_seconds=int(case.input.get("recovery_seconds", 10)),
            half_open_timeout_seconds=5,
        )
        dependency = "evaluation:provider"

        async def fail():
            raise TimeoutError("injected")

        for _ in range(int(case.input.get("failures", 2))):
            try:
                await breaker.call(dependency, fail)
            except TimeoutError:
                pass
        try:
            await breaker.before_call(dependency)
            blocked_while_open = False
        except CircuitOpenError:
            blocked_while_open = True

        now[0] += float(case.input.get("advance_seconds", 11))
        try:
            probe_state = await breaker.before_call(dependency)
        except CircuitOpenError:
            probe_state = "blocked"
        try:
            await breaker.before_call(dependency)
            second_probe_blocked = False
        except CircuitOpenError:
            second_probe_blocked = True
        await breaker.record_success(dependency)
        closed_state = await breaker.before_call(dependency)
        return {
            "blocked_while_open": blocked_while_open,
            "probe_state": probe_state,
            "second_probe_blocked": second_probe_blocked,
            "state_after_success": closed_state,
        }


register(ResourceGovernanceSuite())
