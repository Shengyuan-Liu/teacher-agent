from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.redis_client import get_redis

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@router.get("/health/ready")
async def readiness(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Confirms Postgres (with pgvector) and Redis are reachable."""
    version = (await db.execute(text("SHOW server_version"))).scalar_one()
    has_vector = (
        await db.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        )
    ).scalar_one()

    redis = get_redis()
    await redis.ping()
    await redis.aclose()

    return {
        "status": "ok",
        "checks": {
            "postgres": {"server_version": version, "pgvector": has_vector},
            "redis": {"reachable": True},
        },
    }


@router.get("/capabilities")
async def capabilities() -> dict[str, Any]:
    """Tells the frontend which optional features this deployment has enabled."""
    return {
        "web_search": settings.web_search_enabled,
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "limits": {
            "max_upload_size_mb": settings.max_upload_size_mb,
            "max_repo_size_mb": settings.max_repo_size_mb,
            "max_crawl_pages": settings.max_crawl_pages,
        },
    }
