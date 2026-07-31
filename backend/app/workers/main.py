from arq.connections import RedisSettings

from app.core.config import settings
from app.workers.evaluation import requeue_interrupted_evaluations, run_evaluation_job
from app.workers.ingest import ingest_source, requeue_interrupted


async def recover_interrupted(ctx):
    await requeue_interrupted(ctx)
    await requeue_interrupted_evaluations(ctx)


class WorkerSettings:
    functions = [ingest_source, run_evaluation_job]
    on_startup = recover_interrupted
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = settings.ingest_job_timeout
