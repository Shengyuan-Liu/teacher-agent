from enum import StrEnum
from functools import lru_cache
from typing import TypedDict

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.core.config import settings


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
    reasoning_effort: str


def model_name(tier: IntelligenceTier) -> str:
    override = (
        settings.llm_fast_model if tier is IntelligenceTier.FAST else settings.llm_smart_model
    )
    if override:
        return override
    if settings.llm_provider == "openai":
        return OPENAI_TIER_DEFAULTS[tier]
    return settings.llm_model


def model_trace(tier: IntelligenceTier) -> ModelTrace:
    """Describe the exact configured model a tier resolves to.

    Keeping this next to ``chat_model`` prevents the trace UI from maintaining
    a second, eventually stale copy of the model-routing rules.
    """
    model = model_name(tier)
    detail: ModelTrace = {
        "provider": settings.llm_provider,
        "model": model,
        "tier": tier.value,
    }
    if settings.llm_provider == "openai" and model.startswith("gpt-5"):
        detail["reasoning_effort"] = (
            settings.llm_fast_reasoning_effort
            if tier is IntelligenceTier.FAST
            else settings.llm_smart_reasoning_effort
        )
    return detail


@lru_cache
def chat_model(tier: IntelligenceTier = IntelligenceTier.SMART) -> BaseChatModel:
    selection = model_trace(tier)
    model = selection["model"]
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
            if "reasoning_effort" in selection:
                kwargs["reasoning_effort"] = selection["reasoning_effort"]
            return ChatOpenAI(model=model, api_key=settings.openai_api_key, **kwargs)
        case "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(model=model, base_url=settings.ollama_base_url)


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
