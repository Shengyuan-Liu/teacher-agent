import uuid

from app.services.memory import cleanup_expired_memories, extract_turn_memories


async def extract_user_memories(ctx, assistant_message_id: str) -> int:
    del ctx
    return await extract_turn_memories(uuid.UUID(assistant_message_id))


async def cleanup_user_memories(ctx) -> int:
    del ctx
    return await cleanup_expired_memories()
