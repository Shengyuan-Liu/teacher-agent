"""Cross-encoder reranking of fused candidates.

Fusion orders by how often and how highly a passage appeared, not by whether it
actually answers the question. A reranker reads query and passage together, so
it can demote a passage that merely shares vocabulary.

`jina` is the recommended provider: listwise scoring of the whole candidate set
in one pass at roughly 190ms. `llm` needs no extra account and works with the
chat model already configured, at the cost of latency.
"""

import json
import re
from dataclasses import dataclass
from typing import Protocol

import httpx
import structlog
from langchain_core.messages import HumanMessage

from app.core.config import settings
from app.services import usage
from app.services.providers import IntelligenceTier, chat_model

log = structlog.get_logger()

SNIPPET_CHARS = 1200

LLM_PROMPT = """Rank the passages by how well they answer the question.

Question: {query}

{passages}

Reply with only a JSON array of passage numbers, best first, at most {top_n}
entries, e.g. [3, 1, 7]. Include a number only if the passage is genuinely
relevant; omit the rest."""


@dataclass
class Reranked:
    index: int
    score: float


class Reranker(Protocol):
    async def rank(self, query: str, documents: list[str], top_n: int) -> list[Reranked]: ...


class NoopReranker:
    async def rank(self, query: str, documents: list[str], top_n: int) -> list[Reranked]:
        return [Reranked(index=i, score=1.0 / (i + 1)) for i in range(min(top_n, len(documents)))]


class JinaReranker:
    URL = "https://api.jina.ai/v1/rerank"

    async def rank(self, query: str, documents: list[str], top_n: int) -> list[Reranked]:
        async with httpx.AsyncClient(timeout=settings.rerank_timeout) as client:
            response = await client.post(
                self.URL,
                headers={"Authorization": f"Bearer {settings.jina_api_key}"},
                json={
                    "model": settings.jina_rerank_model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                },
            )
            response.raise_for_status()
            results = response.json()["results"]
        usage.record_flat(
            "rerank", settings.jina_rerank_model, _flat_price(settings.jina_rerank_model)
        )
        return [Reranked(index=r["index"], score=r["relevance_score"]) for r in results]


class CohereReranker:
    URL = "https://api.cohere.com/v2/rerank"

    async def rank(self, query: str, documents: list[str], top_n: int) -> list[Reranked]:
        async with httpx.AsyncClient(timeout=settings.rerank_timeout) as client:
            response = await client.post(
                self.URL,
                headers={"Authorization": f"Bearer {settings.cohere_api_key}"},
                json={
                    "model": settings.cohere_rerank_model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                },
            )
            response.raise_for_status()
            results = response.json()["results"]
        usage.record_flat(
            "rerank", settings.cohere_rerank_model, _flat_price(settings.cohere_rerank_model)
        )
        return [Reranked(index=r["index"], score=r["relevance_score"]) for r in results]


class VoyageReranker:
    URL = "https://api.voyageai.com/v1/rerank"

    async def rank(self, query: str, documents: list[str], top_n: int) -> list[Reranked]:
        async with httpx.AsyncClient(timeout=settings.rerank_timeout) as client:
            response = await client.post(
                self.URL,
                headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                json={
                    "model": settings.voyage_rerank_model,
                    "query": query,
                    "documents": documents,
                    "top_k": top_n,
                },
            )
            response.raise_for_status()
            results = response.json()["data"]
        usage.record_flat(
            "rerank", settings.voyage_rerank_model, _flat_price(settings.voyage_rerank_model)
        )
        return [Reranked(index=r["index"], score=r["relevance_score"]) for r in results]


class LlmReranker:
    """Listwise ranking with the configured chat model: no extra account needed."""

    async def rank(self, query: str, documents: list[str], top_n: int) -> list[Reranked]:
        passages = "\n\n".join(
            f"[{i + 1}] {doc[:SNIPPET_CHARS]}" for i, doc in enumerate(documents)
        )
        prompt = LLM_PROMPT.format(query=query, passages=passages, top_n=top_n)
        reply = await chat_model(IntelligenceTier.FAST).ainvoke([HumanMessage(prompt)])
        usage.record_message("rerank", reply)

        order = _parse_order(reply.text, len(documents))
        if order is None:
            log.warning("rerank.llm_unparsable", reply=reply.text[:120])
            return await NoopReranker().rank(query, documents, top_n)
        return [
            Reranked(index=idx, score=1.0 / (rank + 1)) for rank, idx in enumerate(order[:top_n])
        ]


def _flat_price(model: str) -> float | None:
    return settings.rerank_prices.get(model)


def _parse_order(text: str, count: int) -> list[int] | None:
    """None means the reply was unusable; [] means nothing was relevant."""
    match = re.search(r"\[[^\]]*\]", text, re.S)
    if not match:
        return None
    try:
        numbers = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    seen: list[int] = []
    for n in numbers:
        idx = int(n) - 1
        if 0 <= idx < count and idx not in seen:
            seen.append(idx)
    return seen


def get_reranker() -> Reranker:
    match settings.reranker:
        case "jina":
            return JinaReranker()
        case "cohere":
            return CohereReranker()
        case "voyage":
            return VoyageReranker()
        case "llm":
            return LlmReranker()
        case _:
            return NoopReranker()
