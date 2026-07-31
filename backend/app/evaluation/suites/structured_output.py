"""Deterministic regression suite for model JSON recovery."""

from typing import Any

from app.evaluation.base import EvaluationCase, EvaluationContext, EvaluationOutcome, SuiteInfo
from app.evaluation.registry import register
from app.services.structured_output import StructuredOutputError, parse_json_object


class StructuredOutputSuite:
    info = SuiteInfo(
        name="structured_output",
        description="Validates strict JSON parsing and bounded local recovery.",
        metrics=("contract_accuracy", "parse_success", "exact_match"),
    )

    async def evaluate(self, case: EvaluationCase, context: EvaluationContext) -> EvaluationOutcome:
        del context
        candidate = case.input.get("candidate")
        if not isinstance(candidate, str):
            raise ValueError("structured_output input.candidate must be a string")

        should_be_valid = bool(case.expected.get("valid", True))
        parsed: dict[str, Any] | None = None
        recovery_method: str | None = None
        error: str | None = None
        try:
            result = parse_json_object(candidate)
            parsed = result.value
            recovery_method = result.recovery_method
        except StructuredOutputError as exc:
            error = str(exc)

        parse_success = parsed is not None
        expected_value = case.expected.get("value")
        value_required = "value" in case.expected
        exact_match = parse_success and (not value_required or parsed == expected_value)
        contract_ok = parse_success == should_be_valid and (not should_be_valid or exact_match)

        return EvaluationOutcome(
            passed=contract_ok,
            output={
                "parsed": parsed,
                "recovery_method": recovery_method,
                "error": error,
            },
            scores={
                "contract_accuracy": float(contract_ok),
                "parse_success": float(parse_success),
                "exact_match": float(exact_match),
            },
            details={"expected_valid": should_be_valid},
        )


register(StructuredOutputSuite())
