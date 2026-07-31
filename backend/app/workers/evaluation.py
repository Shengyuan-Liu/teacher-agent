"""ARQ job for durable evaluation runs."""

import uuid
from typing import Any

import structlog
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.evaluation.runner import run_evaluation
from app.models import EvalRun

log = structlog.get_logger()


async def requeue_interrupted_evaluations(ctx: dict[str, Any]) -> None:
    """Resume runs that a previous worker left after it had claimed the job."""
    async with AsyncSessionLocal() as db:
        rows = list(await db.scalars(select(EvalRun).where(EvalRun.status == "running")))
        for row in rows:
            row.status = "pending"
            row.error = None
        await db.commit()
        run_ids = [str(row.id) for row in rows]

    for run_id in run_ids:
        await ctx["redis"].enqueue_job("run_evaluation_job", run_id)
    if run_ids:
        log.info("evaluation.requeued_interrupted", count=len(run_ids))


async def run_evaluation_job(ctx: dict[str, Any], run_id: str) -> None:
    del ctx
    async with AsyncSessionLocal() as db:
        await run_evaluation(db, uuid.UUID(run_id))
