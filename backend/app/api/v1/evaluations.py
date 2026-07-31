import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_owned_workspace
from app.core.config import settings
from app.core.database import get_db
from app.evaluation.fixtures import STARTER_CASES
from app.evaluation.registry import get_suite, list_suites
from app.evaluation.runner import git_sha
from app.models import EvalCase, EvalDataset, EvalResult, EvalRun, User, Workspace
from app.schemas.evaluation import (
    EvalDatasetCreate,
    EvalDatasetResponse,
    EvalRunCreate,
    EvalRunResponse,
    EvalStarterCreate,
    EvalSuiteResponse,
)
from app.services.providers import IntelligenceTier, model_trace
from app.services.queue import get_queue

router = APIRouter(tags=["evaluations"])


def _dataset_payload(row: EvalDataset, *, include_cases: bool = False) -> dict:
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "name": row.name,
        "description": row.description,
        "suite": row.suite,
        "version": row.version,
        "default_config": row.default_config,
        "thresholds": row.thresholds,
        "metadata": row.metadata_json,
        "case_count": len(row.cases),
        "created_at": row.created_at,
        "cases": [
            {
                "id": case.id,
                "key": case.key,
                "position": case.position,
                "input": case.input_json,
                "expected": case.expected_json,
                "tags": case.tags,
                "metadata": case.metadata_json,
                "enabled": case.enabled,
            }
            for case in row.cases
        ]
        if include_cases
        else None,
    }


def _run_payload(row: EvalRun, *, include_results: bool = False) -> dict:
    return {
        "id": row.id,
        "dataset_id": row.dataset_id,
        "workspace_id": row.workspace_id,
        "baseline_run_id": row.baseline_run_id,
        "suite": row.suite,
        "label": row.label,
        "variant": row.variant,
        "status": row.status,
        "config": row.config,
        "summary": row.summary,
        "comparison": row.comparison,
        "git_sha": row.git_sha,
        "error": row.error,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
        "dataset_name": row.dataset.name,
        "results": [
            {
                "id": result.id,
                "case_id": result.case_id,
                "case_key": result.case_key,
                "status": result.status,
                "passed": result.passed,
                "input": result.case.input_json,
                "expected": result.case.expected_json,
                "output": result.output,
                "scores": result.scores,
                "details": result.details,
                "latency_ms": result.latency_ms,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": result.cost_usd,
                "error": result.error,
            }
            for result in row.results
        ]
        if include_results
        else None,
    }


async def _owned_dataset(
    dataset_id: uuid.UUID, workspace: Workspace, user: User, db: AsyncSession
) -> EvalDataset:
    row = await db.scalar(
        select(EvalDataset)
        .options(selectinload(EvalDataset.cases))
        .where(
            EvalDataset.id == dataset_id,
            EvalDataset.workspace_id == workspace.id,
            EvalDataset.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation dataset not found")
    return row


@router.get(
    "/workspaces/{workspace_id}/evals/suites",
    response_model=list[EvalSuiteResponse],
)
async def evaluation_suites(
    workspace: Workspace = Depends(get_owned_workspace),
) -> list[dict]:
    del workspace
    return [
        {
            "name": item.name,
            "description": item.description,
            "metrics": list(item.metrics),
            "requires_workspace": item.requires_workspace,
            "requires_model": item.requires_model,
        }
        for item in list_suites()
    ]


@router.get(
    "/workspaces/{workspace_id}/evals/datasets",
    response_model=list[EvalDatasetResponse],
)
async def list_datasets(
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = list(
        await db.scalars(
            select(EvalDataset)
            .options(selectinload(EvalDataset.cases))
            .where(
                EvalDataset.workspace_id == workspace.id,
                EvalDataset.user_id == user.id,
            )
            .order_by(EvalDataset.created_at.desc())
        )
    )
    return [_dataset_payload(row) for row in rows]


@router.post(
    "/workspaces/{workspace_id}/evals/datasets",
    response_model=EvalDatasetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset(
    body: EvalDatasetCreate,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        get_suite(body.suite)
    except KeyError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown evaluation suite"
        ) from None
    duplicate = await db.scalar(
        select(EvalDataset.id).where(
            EvalDataset.workspace_id == workspace.id,
            EvalDataset.name == body.name,
            EvalDataset.version == body.version,
        )
    )
    if duplicate:
        raise HTTPException(status.HTTP_409_CONFLICT, "Dataset name and version already exist")

    row = EvalDataset(
        workspace_id=workspace.id,
        user_id=user.id,
        name=body.name,
        description=body.description,
        suite=body.suite,
        version=body.version,
        default_config=body.default_config,
        thresholds=body.thresholds,
        metadata_json=body.metadata,
    )
    db.add(row)
    await db.flush()
    for position, case in enumerate(body.cases):
        db.add(
            EvalCase(
                dataset_id=row.id,
                position=position,
                key=case.key,
                input_json=case.input,
                expected_json=case.expected,
                tags=case.tags,
                metadata_json=case.metadata,
                enabled=case.enabled,
            )
        )
    await db.flush()
    await db.refresh(row, ["cases"])
    return _dataset_payload(row, include_cases=True)


@router.post(
    "/workspaces/{workspace_id}/evals/datasets/starter",
    response_model=EvalDatasetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_starter_dataset(
    body: EvalStarterCreate,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    cases = STARTER_CASES.get(body.suite)
    if cases is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This suite does not have a built-in starter dataset",
        )
    name = body.name or f"{body.suite.replace('_', ' ').title()} Golden Set"
    is_coordination = body.suite == "multi_agent_coordination"
    return await create_dataset(
        EvalDatasetCreate(
            name=name,
            description=(
                "Built-in multi-agent coordination and ablation cases."
                if is_coordination
                else "Built-in adversarial contract cases; customize before release."
            ),
            suite=body.suite,
            default_config={"execution_mode": "deterministic"} if is_coordination else {},
            thresholds={
                "min_scores": (
                    {
                        "answer_quality": 0.9,
                        "claim_recall": 1.0,
                        "citation_coverage": 1.0,
                    }
                    if is_coordination
                    else {"contract_accuracy": 1.0}
                ),
                **(
                    {
                        "max_regression": {
                            "answer_quality": 0.02,
                            "latency_efficiency": 0.1,
                        }
                    }
                    if is_coordination
                    else {}
                ),
            },
            metadata={"source": "starter", "distribution": "golden+adversarial"},
            cases=cases,
        ),
        workspace,
        user,
        db,
    )


@router.get(
    "/workspaces/{workspace_id}/evals/datasets/{dataset_id}",
    response_model=EvalDatasetResponse,
)
async def get_dataset(
    dataset_id: uuid.UUID,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return _dataset_payload(
        await _owned_dataset(dataset_id, workspace, user, db), include_cases=True
    )


@router.delete(
    "/workspaces/{workspace_id}/evals/datasets/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_dataset(
    dataset_id: uuid.UUID,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await db.delete(await _owned_dataset(dataset_id, workspace, user, db))


@router.post(
    "/workspaces/{workspace_id}/evals/datasets/{dataset_id}/runs",
    response_model=EvalRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(
    body: EvalRunCreate,
    dataset_id: uuid.UUID,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    dataset = await _owned_dataset(dataset_id, workspace, user, db)
    baseline = None
    if body.baseline_run_id:
        baseline = await db.get(EvalRun, body.baseline_run_id)
        if (
            baseline is None
            or baseline.dataset_id != dataset.id
            or baseline.user_id != user.id
            or baseline.status != "completed"
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Baseline must be a completed run of this dataset",
            )

    row = EvalRun(
        dataset_id=dataset.id,
        workspace_id=workspace.id,
        user_id=user.id,
        baseline_run_id=baseline.id if baseline else None,
        suite=dataset.suite,
        label=body.label,
        variant=body.variant,
        config={
            **body.config,
            "_runtime": {
                "fast": model_trace(IntelligenceTier.FAST),
                "smart": model_trace(IntelligenceTier.SMART),
                "embedding_provider": settings.embedding_provider,
                "embedding_model": settings.embedding_model,
                "reranker": settings.reranker,
            },
        },
        git_sha=git_sha(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    try:
        queue = await get_queue()
        await queue.enqueue_job("run_evaluation_job", str(row.id))
    except Exception as exc:
        row.status = "failed"
        row.error = f"Could not enqueue evaluation: {exc}"[:4000]
        await db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Evaluation worker is unavailable"
        ) from exc
    row.dataset = dataset
    return _run_payload(row)


@router.get(
    "/workspaces/{workspace_id}/evals/runs",
    response_model=list[EvalRunResponse],
)
async def list_runs(
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = list(
        await db.scalars(
            select(EvalRun)
            .options(selectinload(EvalRun.dataset))
            .where(EvalRun.workspace_id == workspace.id, EvalRun.user_id == user.id)
            .order_by(EvalRun.created_at.desc())
            .limit(100)
        )
    )
    return [_run_payload(row) for row in rows]


@router.get(
    "/workspaces/{workspace_id}/evals/runs/{run_id}",
    response_model=EvalRunResponse,
)
async def get_run(
    run_id: uuid.UUID,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await db.scalar(
        select(EvalRun)
        .options(
            selectinload(EvalRun.dataset),
            selectinload(EvalRun.results).selectinload(EvalResult.case),
        )
        .where(
            EvalRun.id == run_id,
            EvalRun.workspace_id == workspace.id,
            EvalRun.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation run not found")
    return _run_payload(row, include_results=True)
