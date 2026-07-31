import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class EvalSuiteResponse(BaseModel):
    name: str
    description: str
    metrics: list[str]
    requires_workspace: bool
    requires_model: bool


class EvalCaseCreate(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    input: dict[str, Any]
    expected: dict[str, Any]
    tags: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class EvalDatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    suite: str = Field(min_length=1, max_length=80)
    version: int = Field(default=1, ge=1)
    default_config: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    cases: list[EvalCaseCreate] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_case_keys(self):
        keys = [case.key for case in self.cases]
        if len(keys) != len(set(keys)):
            raise ValueError("case keys must be unique within a dataset")
        return self


class EvalStarterCreate(BaseModel):
    suite: str
    name: str | None = Field(default=None, min_length=1, max_length=200)


class EvalCaseResponse(BaseModel):
    id: uuid.UUID
    key: str
    position: int
    input: dict[str, Any]
    expected: dict[str, Any]
    tags: list[str]
    metadata: dict[str, Any]
    enabled: bool


class EvalDatasetResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    description: str | None
    suite: str
    version: int
    default_config: dict[str, Any]
    thresholds: dict[str, Any]
    metadata: dict[str, Any]
    case_count: int
    created_at: datetime
    cases: list[EvalCaseResponse] | None = None


class EvalRunCreate(BaseModel):
    label: str = Field(default="run", min_length=1, max_length=200)
    variant: str | None = Field(default=None, max_length=100)
    baseline_run_id: uuid.UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class EvalResultResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    case_key: str
    status: str
    passed: bool | None
    input: dict[str, Any]
    expected: dict[str, Any]
    output: dict[str, Any]
    scores: dict[str, float]
    details: dict[str, Any]
    latency_ms: float | None
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    error: str | None


class EvalRunResponse(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    workspace_id: uuid.UUID
    baseline_run_id: uuid.UUID | None
    suite: str
    label: str
    variant: str | None
    status: str
    config: dict[str, Any]
    summary: dict[str, Any]
    comparison: dict[str, Any]
    git_sha: str | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    dataset_name: str
    results: list[EvalResultResponse] | None = None
