from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    assessments,
    auth,
    chat,
    evaluations,
    health,
    images,
    lectures,
    observability,
    plans,
    prompts,
    sources,
    web_search,
    workspaces,
)
from app.core.config import settings
from app.core.database import engine
from app.core.redis_client import close_redis
from app.services.queue import close_queue
from app.services.telemetry import setup_telemetry, shutdown_telemetry

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", app=settings.app_name, env=settings.environment)
    yield
    await engine.dispose()
    await close_redis()
    await close_queue()
    shutdown_telemetry()


app = FastAPI(
    title=settings.app_name,
    description="Grounded learning assistant - Q&A, study plans, quizzes, explanations, lectures",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url=f"{settings.api_v1_prefix}/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    health.router,
    auth.router,
    workspaces.router,
    sources.router,
    images.router,
    chat.router,
    evaluations.router,
    plans.router,
    lectures.router,
    observability.router,
    prompts.router,
    assessments.router,
    web_search.router,
):
    app.include_router(router, prefix=settings.api_v1_prefix)

setup_telemetry(app)


@app.get("/")
async def root() -> dict[str, str]:
    return {"app": settings.app_name, "docs": "/docs", "health": f"{settings.api_v1_prefix}/health"}
