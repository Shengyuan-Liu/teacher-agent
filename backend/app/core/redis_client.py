import redis.asyncio as aioredis

from app.core.config import settings

pool = aioredis.ConnectionPool.from_url(
    settings.redis_url,
    decode_responses=True,
    max_connections=20,
    socket_connect_timeout=2,
    socket_timeout=5,
)


def get_redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=pool)


async def close_redis() -> None:
    await pool.disconnect()
