import asyncio
import uuid

import pytest

from app.core.config import settings
from app.services import providers, usage
from app.services import resource_governance as governance
from app.services.resource_governance import (
    BudgetLimits,
    CircuitBreaker,
    CircuitOpenError,
    ResourceBudgetExceeded,
    ResourceCache,
    ResourceLedger,
    cache_key,
)


def _model(tier: str) -> str:
    return f"{tier}-model"


def _price(model: str) -> tuple[float, float] | None:
    return {
        "fast-model": (0.1, 0.2),
        "smart-model": (10.0, 20.0),
    }.get(model)


def test_budget_soft_limit_downgrades_smart_before_reserving(monkeypatch):
    monkeypatch.setattr(settings, "resource_budget_enabled", True)
    ledger = ResourceLedger(
        limits=BudgetLimits(
            max_model_calls=10,
            max_tokens=100_000,
            max_cost_usd=0.03,
            soft_ratio=0.5,
            estimated_input_tokens=1_000,
            estimated_output_tokens=1_000,
        )
    )

    selected = ledger.reserve_model("smart", _model, _price)

    assert selected == "fast"
    assert ledger.downgrade_count == 1
    assert ledger.reservations[0].model == "fast-model"


def test_budget_reservations_prevent_parallel_oversubscription(monkeypatch):
    monkeypatch.setattr(settings, "resource_budget_enabled", True)
    ledger = ResourceLedger(
        limits=BudgetLimits(
            max_model_calls=1,
            max_tokens=100_000,
            max_cost_usd=10.0,
            soft_ratio=0.8,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
    )
    ledger.reserve_model("fast", _model, _price)

    with pytest.raises(ResourceBudgetExceeded):
        ledger.reserve_model("fast", _model, _price)

    assert ledger.hard_stop is True


def test_budget_reconciles_provider_usage_and_keeps_unknown_cost_explicit(monkeypatch):
    monkeypatch.setattr(settings, "resource_budget_enabled", True)
    ledger = ResourceLedger()
    ledger.reserve_model("fast", _model, _price)
    ledger.reconcile_usage(
        input_tokens=321,
        output_tokens=45,
        cost_usd=None,
        call_kind="model",
    )

    payload = ledger.as_payload()["budget"]
    assert payload["actual"]["model_calls"] == 1
    assert payload["actual"]["input_tokens"] == 321
    assert payload["reserved_model_calls"] == 0
    assert payload["cost_fully_enforced"] is False


def test_cache_keys_are_stable_but_workspace_isolated():
    first = uuid.uuid4()
    second = uuid.uuid4()
    payload = {"query": "private question", "model": "fast-model"}

    key = cache_key("router", first, payload)
    assert key == cache_key("router", first, payload)
    assert key != cache_key("router", second, payload)
    assert "private question" not in key
    assert str(first) not in key


class _FakeRedis:
    values: dict[str, str] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int):
        assert ex > 0
        self.values[key] = value

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_cache_single_flight_reuses_one_workspace_result(monkeypatch):
    _FakeRedis.values = {}
    monkeypatch.setattr(settings, "resource_cache_enabled", True)
    monkeypatch.setattr(governance, "get_redis", _FakeRedis)
    monkeypatch.setattr(governance, "_redis_unavailable_until", 0.0)
    cache = ResourceCache()
    workspace_id = uuid.uuid4()
    calls = 0

    async def compute():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"intent": "qa"}

    results = await asyncio.gather(
        *(
            cache.get_or_compute(
                namespace="router",
                workspace_id=workspace_id,
                key_payload={"question": "same"},
                ttl_seconds=30,
                compute=compute,
            )
            for _ in range(5)
        )
    )

    assert results == [{"intent": "qa"}] * 5
    assert calls == 1


@pytest.mark.asyncio
async def test_circuit_opens_allows_one_half_open_probe_and_closes(monkeypatch):
    monkeypatch.setattr(settings, "circuit_breaker_enabled", True)
    now = [100.0]
    breaker = CircuitBreaker(
        use_redis=False,
        clock=lambda: now[0],
        failure_threshold=2,
        failure_window_seconds=60,
        recovery_seconds=10,
        half_open_timeout_seconds=5,
    )

    async def fail():
        raise TimeoutError("provider timeout")

    for _ in range(2):
        with pytest.raises(TimeoutError):
            await breaker.call("llm:test", fail)

    with pytest.raises(CircuitOpenError):
        await breaker.before_call("llm:test")

    now[0] += 11
    assert await breaker.before_call("llm:test") == "half_open"
    with pytest.raises(CircuitOpenError):
        await breaker.before_call("llm:test")

    await breaker.record_success("llm:test")
    assert await breaker.before_call("llm:test") == "closed"


@pytest.mark.asyncio
async def test_model_proxy_blocks_before_second_provider_io(monkeypatch):
    calls: list[str] = []

    class Model:
        def __init__(self, tier: str):
            self.tier = tier

        async def ainvoke(self, _messages):
            calls.append(self.tier)
            return object()

    class PassBreaker:
        async def call(self, _dependency, operation):
            return await operation()

    monkeypatch.setattr(settings, "resource_budget_enabled", True)
    monkeypatch.setattr(settings, "turn_budget_max_model_calls", 1)
    monkeypatch.setattr(settings, "turn_budget_max_tokens", 100_000)
    monkeypatch.setattr(settings, "turn_budget_max_cost_usd", 10.0)
    monkeypatch.setattr(providers, "_build_chat_model", lambda tier: Model(tier.value))
    monkeypatch.setattr(providers, "circuit_breaker", PassBreaker())
    providers.chat_model.cache_clear()
    usage.start()

    model = providers.chat_model(providers.IntelligenceTier.FAST)
    await model.ainvoke([])
    with pytest.raises(ResourceBudgetExceeded):
        await model.ainvoke([])

    assert calls == ["fast"]
    providers.chat_model.cache_clear()
    governance._ledger.set(None)
    usage._ledger.set(None)
