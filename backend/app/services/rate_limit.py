"""Per-user rate limiting for the web-search endpoints.

A fixed-window counter in Redis: cheap, and good enough to keep an explicit,
user-driven feature from being hammered. Search bills per call, so this also
caps cost.
"""

import time
import uuid

from app.services.queue import get_queue


async def over_rate_limit(user_id: uuid.UUID, bucket: str, limit: int, window_seconds: int) -> bool:
    redis = await get_queue()
    window = int(time.time()) // window_seconds
    key = f"ratelimit:{bucket}:{user_id}:{window}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    return count > limit
