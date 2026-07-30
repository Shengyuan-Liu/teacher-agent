"""Web search endpoints (docs/05-api-design.md 5.1).

Every route here is a no-op unless the deployment enabled web search, and the
search itself only runs on these explicit calls. Ingestion is split from search
into a second, separately-confirmed request: that split is the human-in-the-loop
gate that keeps "search -> ingest" from ever becoming an unattended pipeline.
"""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.search import search_candidates
from app.api.deps import get_current_user, get_owned_workspace
from app.api.v1.sources import create_remote_source
from app.core.config import settings
from app.core.database import get_db
from app.models import SourceProvenance, SourceType, User, Workspace
from app.rag.search import WebSearchError
from app.schemas.web_search import (
    WebSearchIngestRequest,
    WebSearchIngestResponse,
    WebSearchRequest,
    WebSearchResponse,
)
from app.services.rate_limit import over_rate_limit

# The web_search flag is reported by GET /capabilities (see health.py).
router = APIRouter(tags=["web_search"])


def _assert_enabled() -> None:
    if not settings.web_search_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "WEB_SEARCH_DISABLED")


@router.post("/workspaces/{workspace_id}/web-search", response_model=WebSearchResponse)
async def web_search(
    body: WebSearchRequest,
    workspace: Workspace = Depends(get_owned_workspace),
    user: User = Depends(get_current_user),
) -> WebSearchResponse:
    _assert_enabled()
    intent = (body.query or body.from_question or "").strip()
    if not intent:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "query or from_question is required"
        )
    if await over_rate_limit(user.id, "web_search", settings.web_search_rate_limit_per_hour, 3600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "RATE_LIMITED")
    try:
        result = await search_candidates(
            workspace.name, intent, body.top_k or settings.web_search_top_k, body.site_filter
        )
    except WebSearchError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "WEB_SEARCH_FAILED") from exc
    return WebSearchResponse(**result)


@router.post(
    "/workspaces/{workspace_id}/web-search/ingest",
    response_model=WebSearchIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def web_search_ingest(
    body: WebSearchIngestRequest,
    workspace: Workspace = Depends(get_owned_workspace),
    db: AsyncSession = Depends(get_db),
) -> WebSearchIngestResponse:
    _assert_enabled()
    source_ids = []
    for item in body.results:
        url = str(item.url)
        source = await create_remote_source(
            db,
            workspace,
            SourceType.URL,
            url,
            item.title or urlsplit(url).netloc,
            SourceProvenance.WEB_SEARCH,
            body.query,
        )
        source_ids.append(source.id)
    return WebSearchIngestResponse(source_ids=source_ids)
