"""Web search for form B (docs/02-features.md 2.9, 06-agent-design.md 4.2).

Given the user's intent and the workspace's topics, generate queries, search,
and return de-duplicated ranked candidates for the user to pick from. Nothing
is ingested here: ingestion is a separate, explicitly confirmed request, which
is the human-in-the-loop gate. Query generation and ranking are plain LLM
calls, not a graph — there is no state to pause and resume until the Phase 4
checkpointer lands.
"""

import asyncio
import json
import re
import uuid

from langchain_core.messages import HumanMessage

from app.rag.search import SearchResult, cached_search, get_search_provider
from app.services import usage
from app.services.providers import IntelligenceTier, chat_model

QUERY_PROMPT = """You are preparing web searches to supplement a study workspace.

Workspace: {workspace}
The learner wants material on: {intent}

Produce 1 to 3 focused web search queries that add the workspace's domain context
to the request (a bare question often lacks it). Output only a JSON array of
strings, e.g. ["query one", "query two"]."""

RANK_PROMPT = """The learner wants web material on: {intent}

Candidate search results:

{candidates}

Return a JSON array naming the candidates worth adding to the workspace, most
useful first: [{{"index": 0, "reason": "short phrase on why it fits"}}]
Omit low-quality, off-topic, or redundant results."""


async def search_candidates(
    workspace: str,
    intent: str,
    top_k: int,
    site_filter: list[str] | None = None,
    *,
    workspace_id: uuid.UUID | None = None,
) -> dict:
    queries = await _build_queries(workspace, intent)
    provider = get_search_provider()
    batches = await asyncio.gather(
        *(
            cached_search(
                provider,
                workspace_id=workspace_id,
                query=query,
                top_k=top_k,
                site_filter=site_filter,
            )
            for query in queries
        )
    )
    merged = _dedup(list(batches))[:top_k]
    ranked = await _rank(intent, merged) if merged else []
    return {"queries_used": queries, "results": ranked}


async def _build_queries(workspace: str, intent: str) -> list[str]:
    reply = await chat_model(IntelligenceTier.FAST).ainvoke(
        [HumanMessage(QUERY_PROMPT.format(workspace=workspace, intent=intent))]
    )
    usage.record_message("web_queries", reply)
    return _parse_list(reply.text)[:3] or [intent]


async def _rank(intent: str, candidates: list[SearchResult]) -> list[dict]:
    listing = "\n".join(
        f"[{i}] {c.title} — {c.domain}\n{c.snippet[:200]}" for i, c in enumerate(candidates)
    )
    reply = await chat_model(IntelligenceTier.FAST).ainvoke(
        [HumanMessage(RANK_PROMPT.format(intent=intent, candidates=listing))]
    )
    usage.record_message("web_rank", reply)
    reasons = {
        obj["index"]: str(obj.get("reason") or "")
        for obj in _parse_objs(reply.text)
        if isinstance(obj.get("index"), int)
    }
    results = [
        {
            "url": c.url,
            "title": c.title,
            "snippet": c.snippet,
            "domain": c.domain,
            "recommended": i in reasons,
            "reason": reasons.get(i),
        }
        for i, c in enumerate(candidates)
    ]
    results.sort(key=lambda r: not r["recommended"])  # recommended first, order kept otherwise
    return results


def _dedup(batches: list[list[SearchResult]]) -> list[SearchResult]:
    seen: set[str] = set()
    merged: list[SearchResult] = []
    for batch in batches:
        for result in batch:
            if result.url not in seen:
                seen.add(result.url)
                merged.append(result)
    return merged


def _parse_list(text: str) -> list[str]:
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return [s for s in parsed if isinstance(s, str) and s.strip()]


def _parse_objs(text: str) -> list[dict]:
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return [obj for obj in parsed if isinstance(obj, dict)]
