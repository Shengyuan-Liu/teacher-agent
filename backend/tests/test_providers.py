import sys
from types import ModuleType

import pytest

from app.core.config import settings
from app.services.providers import IntelligenceTier, chat_model, model_name, model_trace


@pytest.fixture(autouse=True)
def clear_model_cache():
    chat_model.cache_clear()
    yield
    chat_model.cache_clear()


def test_openai_uses_family_defaults_by_tier(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_fast_model", None)
    monkeypatch.setattr(settings, "llm_smart_model", None)

    assert model_name(IntelligenceTier.FAST) == "gpt-5.6-luna"
    assert model_name(IntelligenceTier.SMART) == "gpt-5.6-terra"


def test_explicit_tier_models_override_provider_defaults(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_fast_model", "fast-custom")
    monkeypatch.setattr(settings, "llm_smart_model", "smart-custom")

    assert model_name(IntelligenceTier.FAST) == "fast-custom"
    assert model_name(IntelligenceTier.SMART) == "smart-custom"


def test_other_providers_fall_back_to_legacy_model(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_model", "claude-default")
    monkeypatch.setattr(settings, "llm_fast_model", None)
    monkeypatch.setattr(settings, "llm_smart_model", None)

    assert model_name(IntelligenceTier.FAST) == "claude-default"
    assert model_name(IntelligenceTier.SMART) == "claude-default"


def test_openai_tiers_set_model_and_reasoning_effort(monkeypatch):
    created = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)

    module = ModuleType("langchain_openai")
    module.ChatOpenAI = FakeChatOpenAI
    monkeypatch.setitem(sys.modules, "langchain_openai", module)
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_fast_model", None)
    monkeypatch.setattr(settings, "llm_smart_model", None)
    monkeypatch.setattr(settings, "llm_fast_reasoning_effort", "none")
    monkeypatch.setattr(settings, "llm_smart_reasoning_effort", "medium")

    chat_model(IntelligenceTier.FAST)
    chat_model(IntelligenceTier.SMART)

    assert created == [
        {"model": "gpt-5.6-luna", "api_key": settings.openai_api_key, "reasoning_effort": "none"},
        {
            "model": "gpt-5.6-terra",
            "api_key": settings.openai_api_key,
            "reasoning_effort": "medium",
        },
    ]


def test_model_trace_exposes_exact_tier_selection(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_fast_model", "gpt-5.6-luna")
    monkeypatch.setattr(settings, "llm_fast_reasoning_effort", "none")

    assert model_trace(IntelligenceTier.FAST) == {
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "tier": "fast",
        "reasoning_effort": "none",
    }
