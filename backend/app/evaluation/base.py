"""Contracts shared by deterministic, retrieval and model-based eval suites."""

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class EvaluationCase:
    key: str
    input: dict[str, Any]
    expected: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationContext:
    workspace_id: uuid.UUID | None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationOutcome:
    passed: bool
    output: dict[str, Any]
    scores: dict[str, float]
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SuiteInfo:
    name: str
    description: str
    metrics: tuple[str, ...]
    requires_workspace: bool = False
    requires_model: bool = False


class EvaluationSuite(Protocol):
    info: SuiteInfo

    async def evaluate(
        self, case: EvaluationCase, context: EvaluationContext
    ) -> EvaluationOutcome: ...
