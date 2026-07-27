from arq.connections import RedisSettings

from app.core.config import settings
from app.workers.ingest import ingest_source, requeue_interrupted


class WorkerSettings:
    functions = [ingest_source]
    on_startup = requeue_interrupted
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = settings.ingest_job_timeout
