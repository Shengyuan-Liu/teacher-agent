"""Provider construction and the Fast/Smart model-routing enforcement point.

Agents request an intelligence tier rather than a provider model. The transparent
proxy reserves turn budget and checks the shared circuit breaker immediately
before network I/O, which matters because model instances are cached and DAG
nodes may invoke them concurrently. ``model_trace`` mirrors the same resolution
logic for the UI without consuming budget.
"""

from collections.abc import AsyncIterator
from enum import StrEnum
from functools import lru_cache
from typing import Any, TypedDict, cast

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.core.config import settings
from app.services.resource_governance import (
    ResourceBudgetExceeded,
    circuit_breaker,
    preview_model_tier,
    reserve_model_call,
)


class IntelligenceTier(StrEnum):
    FAST = "fast"
    SMART = "smart"


OPENAI_TIER_DEFAULTS = {
    IntelligenceTier.FAST: "gpt-5.6-luna",
    IntelligenceTier.SMART: "gpt-5.6-terra",
}


class ModelTrace(TypedDict, total=False):
    """The model selection shown alongside an agent step in the call chain."""

    provider: str
    model: str
    tier: str
    requested_tier: str
    reasoning_effort: str
    governance_action: str


def model_name(tier: IntelligenceTier) -> str:
    override = (
        settings.llm_fast_model if tier is IntelligenceTier.FAST else settings.llm_smart_model
    )
    if override:
        return override
    if settings.llm_provider == "openai":
        return OPENAI_TIER_DEFAULTS[tier]
    return settings.llm_model


def _model_for_tier(tier: str) -> str:
    return model_name(IntelligenceTier(tier))


def _price_for_model(model: str) -> tuple[float, float] | None:
    return settings.model_prices.get(model)


def model_trace(tier: IntelligenceTier) -> ModelTrace:
    """Describe the exact configured model a tier resolves to.

    Keeping this next to ``chat_model`` prevents the trace UI from maintaining
    a second, eventually stale copy of the model-routing rules.
    """
    selected_tier = IntelligenceTier(
        preview_model_tier(tier.value, _model_for_tier, _price_for_model)
    )
    model = model_name(selected_tier)
    detail: ModelTrace = {
        "provider": settings.llm_provider,
        "model": model,
        "tier": selected_tier.value,
    }
    if selected_tier is not tier:
        detail["requested_tier"] = tier.value
        detail["governance_action"] = "budget_downgrade"
    if settings.llm_provider == "openai" and model.startswith("gpt-5"):
        detail["reasoning_effort"] = (
            settings.llm_fast_reasoning_effort
            if selected_tier is IntelligenceTier.FAST
            else settings.llm_smart_reasoning_effort
        )
    return detail


def _build_chat_model(tier: IntelligenceTier) -> BaseChatModel:
    model = model_name(tier)
    match settings.llm_provider:
        case "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=model,
                api_key=settings.anthropic_api_key,
                max_tokens=4096,
            )
        case "openai":
            from langchain_openai import ChatOpenAI

            kwargs = {}
            if model.startswith("gpt-5"):
                kwargs["reasoning_effort"] = (
                    settings.llm_fast_reasoning_effort
                    if tier is IntelligenceTier.FAST
                    else settings.llm_smart_reasoning_effort
                )
            return ChatOpenAI(model=model, api_key=settings.openai_api_key, **kwargs)
        case "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(model=model, base_url=settings.ollama_base_url)
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


class _GovernedChatModel:
    """Small transparent proxy around LangChain models.

    The proxy keeps existing Agent call sites and test seams intact while
    enforcing reservation and circuit-breaker checks at the actual I/O edge.
    """

    def __init__(self, requested_tier: IntelligenceTier) -> None:
        self.requested_tier = requested_tier
        self._primary = _build_chat_model(requested_tier)

    def _selection(self) -> tuple[BaseChatModel, str]:
        # Reserve here—not when the cached proxy is constructed—so every actual
        # invocation observes the current turn ledger and concurrent reservations.
        selected = IntelligenceTier(
            reserve_model_call(
                self.requested_tier.value,
                _model_for_tier,
                _price_for_model,
            )
        )
        model = self._primary if selected is self.requested_tier else chat_model(selected)._primary
        dependency = f"llm:{settings.llm_provider}:{model_name(selected)}"
        return model, dependency

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        model, dependency = self._selection()
        return await circuit_breaker.call(
            dependency,
            lambda: model.ainvoke(*args, **kwargs),
        )

    async def astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        model, dependency = self._selection()
        await circuit_breaker.before_call(dependency)
        try:
            async for chunk in model.astream(*args, **kwargs):
                yield chunk
        except ResourceBudgetExceeded:
            raise
        except Exception as exc:
            await circuit_breaker.record_failure(dependency, exc)
            raise
        else:
            await circuit_breaker.record_success(dependency)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._primary, name)


@lru_cache
def chat_model(tier: IntelligenceTier = IntelligenceTier.SMART) -> BaseChatModel:
    # The proxy intentionally presents the same public type as the provider
    # object; current Agents only use ainvoke/astream and delegated attributes.
    return cast(BaseChatModel, _GovernedChatModel(tier))


@lru_cache
def embeddings() -> Embeddings:
    match settings.embedding_provider:
        case "openai":
            from langchain_openai import OpenAIEmbeddings

            return OpenAIEmbeddings(
                model=settings.embedding_model,
                api_key=settings.openai_api_key,
                dimensions=settings.embedding_dimensions,
            )
        case "ollama":
            from langchain_ollama import OllamaEmbeddings

            return OllamaEmbeddings(
                model=settings.embedding_model,
                base_url=settings.ollama_base_url,
            )
