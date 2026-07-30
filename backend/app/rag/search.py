"""Web search provider abstraction.

Only ever invoked on an explicit user action. This module is never bound into
an agent's default tool set — the QA graph exposes it solely on the turn the
user opted in (see docs/02-features.md 2.9 and the red line in CLAUDE.md).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    domain: str


class WebSearchError(RuntimeError):
    """Provider unreachable, timed out, or misconfigured. Maps to
    WEB_SEARCH_FAILED at the API layer and must not break local QA."""


class SearchProvider(ABC):
    @abstractmethod
    async def search(
        self, query: str, top_k: int, site_filter: list[str] | None = None
    ) -> list[SearchResult]: ...


class TavilyProvider(SearchProvider):
    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(
        self, query: str, top_k: int, site_filter: list[str] | None = None
    ) -> list[SearchResult]:
        payload: dict = {"query": query, "max_results": top_k, "search_depth": "basic"}
        if site_filter:
            payload["include_domains"] = site_filter
        try:
            async with httpx.AsyncClient(timeout=settings.web_search_timeout) as client:
                resp = await client.post(
                    self.ENDPOINT,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise WebSearchError(str(exc)) from exc

        results = []
        for item in data.get("results", []):
            url = item.get("url")
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=item.get("title") or url,
                    snippet=item.get("content", ""),
                    domain=urlsplit(url).netloc,
                )
            )
        return results


def get_search_provider() -> SearchProvider:
    """Build the configured provider. The caller is responsible for having
    confirmed web search is enabled and the request carried explicit intent."""
    if settings.search_provider == "tavily":
        if not settings.tavily_api_key:
            raise WebSearchError("TAVILY_API_KEY is not configured")
        return TavilyProvider(settings.tavily_api_key)
    raise WebSearchError(f"search provider '{settings.search_provider}' is not implemented")
