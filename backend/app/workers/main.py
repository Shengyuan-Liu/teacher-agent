from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.workers.evaluation import requeue_interrupted_evaluations, run_evaluation_job
from app.workers.ingest import ingest_source, requeue_interrupted
from app.workers.memory import cleanup_user_memories, extract_user_memories


async def recover_interrupted(ctx):
    await requeue_interrupted(ctx)
    await requeue_interrupted_evaluations(ctx)


class WorkerSettings:
    functions = [ingest_source, run_evaluation_job, extract_user_memories]
    cron_jobs = [cron(cleanup_user_memories, hour=3, minute=15)]
    on_startup = recover_interrupted
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = settings.ingest_job_timeout
