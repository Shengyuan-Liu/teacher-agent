from arq.connections import RedisSettings

from app.core.config import settings
from app.workers.ingest import ingest_source


class WorkerSettings:
    functions = [ingest_source]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = 600
