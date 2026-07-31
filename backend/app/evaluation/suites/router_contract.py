"""Deterministic contract tests for Router output parsing and task plans."""

from app.agents.router import parse_decision
from app.evaluation.base import EvaluationCase, EvaluationContext, EvaluationOutcome, SuiteInfo
from app.evaluation.registry import register


class RouterContractSuite:
    info = SuiteInfo(
        name="router_contract",
        description="Checks Router JSON parsing, intent selection and multi-agent task ordering.",
        metrics=("contract_accuracy", "intent_accuracy", "task_plan_accuracy"),
    )

    async def evaluate(self, case: EvaluationCase, context: EvaluationContext) -> EvaluationOutcome:
        del context
        candidate = case.input.get("candidate")
        if not isinstance(candidate, str):
            raise ValueError("router_contract input.candidate must be a string")

        decision = parse_decision(candidate)
        actual_tasks = [{"agent": task.agent, "query": task.query} for task in decision.tasks]
        expected_intent = case.expected.get("intent")
        expected_tasks = case.expected.get("tasks")
        expected_clarification = case.expected.get("needs_clarification")

        intent_ok = expected_intent is None or decision.intent == expected_intent
        tasks_ok = expected_tasks is None or actual_tasks == expected_tasks
        clarification_ok = expected_clarification is None or decision.needs_clarification is bool(
            expected_clarification
        )
        contract_ok = intent_ok and tasks_ok and clarification_ok

        return EvaluationOutcome(
            passed=contract_ok,
            output={
                "intent": decision.intent,
                "confidence": decision.confidence,
                "alternatives": list(decision.alternatives),
                "reason": decision.reason,
                "needs_clarification": decision.needs_clarification,
                "tasks": actual_tasks,
            },
            scores={
                "contract_accuracy": float(contract_ok),
                "intent_accuracy": float(intent_ok),
                "task_plan_accuracy": float(tasks_ok),
            },
            details={"clarification_accuracy": float(clarification_ok)},
        )


register(RouterContractSuite())
