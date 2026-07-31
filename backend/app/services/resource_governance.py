"""Request-scoped budgets, workspace caches and dependency circuit breakers.

The three controls share one event ledger because they answer the same
operational question: why did this turn spend, reuse, downgrade or stop?
Payloads deliberately contain hashes and dependency names, never user text.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, TypeVar

import structlog

from app.core.config import settings
from app.core.redis_client import get_redis

log = structlog.get_logger()
T = TypeVar("T")


class ResourceBudgetExceeded(RuntimeError):
    """A new provider call would exceed a hard per-turn resource limit."""


class CircuitOpenError(RuntimeError):
    """A dependency is open or already has a half-open probe in flight."""


@dataclass(frozen=True)
class BudgetLimits:
    max_model_calls: int
    max_tokens: int
    max_cost_usd: float
    soft_ratio: float
    estimated_input_tokens: int
    estimated_output_tokens: int

    @classmethod
    def configured(cls) -> BudgetLimits:
        return cls(
            max_model_calls=settings.turn_budget_max_model_calls,
            max_tokens=settings.turn_budget_max_tokens,
            max_cost_usd=settings.turn_budget_max_cost_usd,
            soft_ratio=settings.turn_budget_soft_ratio,
            estimated_input_tokens=settings.turn_budget_estimated_input_tokens,
            estimated_output_tokens=settings.turn_budget_estimated_output_tokens,
        )


@dataclass
class ModelReservation:
    requested_tier: str
    selected_tier: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


@dataclass
class ResourceLedger:
    workspace_id: uuid.UUID | None = None
    limits: BudgetLimits = field(default_factory=BudgetLimits.configured)
    actual_model_calls: int = 0
    actual_input_tokens: int = 0
    actual_output_tokens: int = 0
    actual_cost_usd: float = 0.0
    has_unpriced_usage: bool = False
    reservations: list[ModelReservation] = field(default_factory=list)
    downgrade_count: int = 0
    hard_stop: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)

    def record_event(self, kind: str, **payload: Any) -> None:
        # Keep persisted Message/AgentRun rows bounded even if a provider loops.
        if len(self.events) >= 100:
            return
        self.events.append({"kind": kind, **payload})

    def _estimate(
        self,
        tier: str,
        model_for_tier: Callable[[str], str],
        price_for_model: Callable[[str], tuple[float, float] | None],
    ) -> ModelReservation:
        model = model_for_tier(tier)
        rate = price_for_model(model)
        estimated_cost = (
            (
                self.limits.estimated_input_tokens * rate[0]
                + self.limits.estimated_output_tokens * rate[1]
            )
            / 1_000_000
            if rate is not None
            else None
        )
        return ModelReservation(
            requested_tier=tier,
            selected_tier=tier,
            model=model,
            input_tokens=self.limits.estimated_input_tokens,
            output_tokens=self.limits.estimated_output_tokens,
            cost_usd=estimated_cost,
        )

    def _projected(self, extra: ModelReservation | None = None) -> dict[str, float]:
        reservations = [*self.reservations, *([extra] if extra is not None else [])]
        reserved_costs = [item.cost_usd for item in reservations]
        projected_cost = self.actual_cost_usd + sum(item or 0.0 for item in reserved_costs)
        return {
            "model_calls": float(self.actual_model_calls + len(reservations)),
            "tokens": float(
                self.actual_input_tokens
                + self.actual_output_tokens
                + sum(item.input_tokens + item.output_tokens for item in reservations)
            ),
            "cost_usd": projected_cost,
            "cost_known": float(
                not self.has_unpriced_usage and all(item is not None for item in reserved_costs)
            ),
        }

    def _ratio(self, projected: dict[str, float]) -> float:
        ratios = [
            projected["model_calls"] / self.limits.max_model_calls,
            projected["tokens"] / self.limits.max_tokens,
        ]
        if projected["cost_known"]:
            ratios.append(projected["cost_usd"] / self.limits.max_cost_usd)
        return max(ratios)

    def preview_tier(
        self,
        requested_tier: str,
        model_for_tier: Callable[[str], str],
        price_for_model: Callable[[str], tuple[float, float] | None],
    ) -> str:
        if not settings.resource_budget_enabled or requested_tier != "smart":
            return requested_tier
        if model_for_tier("smart") == model_for_tier("fast"):
            # A nominal tier change that resolves to the same provider model
            # does not save money or intelligence and would make traces lie.
            return requested_tier
        smart = self._estimate("smart", model_for_tier, price_for_model)
        return "fast" if self._ratio(self._projected(smart)) >= self.limits.soft_ratio else "smart"

    def reserve_model(
        self,
        requested_tier: str,
        model_for_tier: Callable[[str], str],
        price_for_model: Callable[[str], tuple[float, float] | None],
    ) -> str:
        if not settings.resource_budget_enabled:
            return requested_tier
        selected_tier = self.preview_tier(requested_tier, model_for_tier, price_for_model)
        reservation = self._estimate(selected_tier, model_for_tier, price_for_model)
        reservation.requested_tier = requested_tier
        reservation.selected_tier = selected_tier
        projected = self._projected(reservation)
        if self._ratio(projected) > 1.0:
            self.hard_stop = True
            self.record_event(
                "budget",
                action="block",
                requested_tier=requested_tier,
                reason="hard_limit",
                projected={
                    "model_calls": int(projected["model_calls"]),
                    "tokens": int(projected["tokens"]),
                    "cost_usd": round(projected["cost_usd"], 6),
                },
            )
            raise ResourceBudgetExceeded(
                "This turn reached its model-call, token, or cost budget. "
                "Start a new turn or raise the configured limit."
            )
        if selected_tier != requested_tier:
            self.downgrade_count += 1
        self.reservations.append(reservation)
        self.record_event(
            "budget",
            action="reserve",
            requested_tier=requested_tier,
            selected_tier=selected_tier,
            model=reservation.model,
            estimated_tokens=reservation.input_tokens + reservation.output_tokens,
            estimated_cost_usd=(
                round(reservation.cost_usd, 6) if reservation.cost_usd is not None else None
            ),
        )
        return selected_tier

    def reconcile_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        call_kind: str,
    ) -> None:
        if call_kind == "model":
            self.actual_model_calls += 1
            if self.reservations:
                self.reservations.pop(0)
        self.actual_input_tokens += input_tokens
        self.actual_output_tokens += output_tokens
        if cost_usd is None:
            self.has_unpriced_usage = True
        else:
            self.actual_cost_usd += cost_usd
        projected = self._projected()
        if (
            projected["model_calls"] > self.limits.max_model_calls
            or projected["tokens"] > self.limits.max_tokens
            or (projected["cost_known"] and projected["cost_usd"] > self.limits.max_cost_usd)
        ):
            # A single provider response can exceed its reservation. It cannot
            # be recalled, but this prevents every subsequent provider call.
            self.hard_stop = True

    def as_payload(self) -> dict[str, Any]:
        projected = self._projected()
        cache_events = [item for item in self.events if item["kind"] == "cache"]
        circuit_events = [item for item in self.events if item["kind"] == "circuit"]
        budget_events = [item for item in self.events if item["kind"] == "budget"]
        return {
            "policy_version": "1.0.0",
            "workspace_scoped": self.workspace_id is not None,
            "budget": {
                "enabled": settings.resource_budget_enabled,
                "limits": asdict(self.limits),
                "actual": {
                    "model_calls": self.actual_model_calls,
                    "input_tokens": self.actual_input_tokens,
                    "output_tokens": self.actual_output_tokens,
                    "cost_usd": round(self.actual_cost_usd, 6),
                },
                "reserved_model_calls": len(self.reservations),
                "projected": {
                    "model_calls": int(projected["model_calls"]),
                    "tokens": int(projected["tokens"]),
                    "cost_usd": round(projected["cost_usd"], 6),
                },
                "cost_fully_enforced": bool(projected["cost_known"]),
                "downgraded_calls": self.downgrade_count,
                "hard_stop": self.hard_stop,
                "events": budget_events,
            },
            "cache": {
                "enabled": settings.resource_cache_enabled,
                "hits": sum(item.get("action") == "hit" for item in cache_events),
                "misses": sum(item.get("action") == "miss" for item in cache_events),
                "bypasses": sum(item.get("action") == "bypass" for item in cache_events),
                "errors": sum(item.get("action") == "error" for item in cache_events),
                "events": cache_events,
            },
            "circuit_breaker": {
                "enabled": settings.circuit_breaker_enabled,
                "events": circuit_events,
            },
        }


_ledger: contextvars.ContextVar[ResourceLedger | None] = contextvars.ContextVar(
    "resource_governance", default=None
)


def start_turn(workspace_id: uuid.UUID | None = None) -> ResourceLedger:
    ledger = ResourceLedger(workspace_id=workspace_id)
    _ledger.set(ledger)
    return ledger


def current() -> ResourceLedger | None:
    return _ledger.get()


def preview_model_tier(
    requested_tier: str,
    model_for_tier: Callable[[str], str],
    price_for_model: Callable[[str], tuple[float, float] | None],
) -> str:
    ledger = current()
    if ledger is None:
        return requested_tier
    return ledger.preview_tier(requested_tier, model_for_tier, price_for_model)


def reserve_model_call(
    requested_tier: str,
    model_for_tier: Callable[[str], str],
    price_for_model: Callable[[str], tuple[float, float] | None],
) -> str:
    ledger = current()
    if ledger is None:
        return requested_tier
    return ledger.reserve_model(requested_tier, model_for_tier, price_for_model)


def reconcile_usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None,
    call_kind: str = "model",
) -> None:
    ledger = current()
    if ledger is not None:
        ledger.reconcile_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            call_kind=call_kind,
        )


def resource_payload() -> dict[str, Any]:
    ledger = current()
    return ledger.as_payload() if ledger is not None else {}


def _record(kind: str, **payload: Any) -> None:
    ledger = current()
    if ledger is not None:
        ledger.record_event(kind, **payload)


def cache_key(namespace: str, workspace_id: uuid.UUID, payload: Any) -> str:
    """Build a non-reversible, tenant-isolated Redis key."""

    workspace_hash = hashlib.sha256(str(workspace_id).encode()).hexdigest()[:16]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
    return f"governance:cache:v1:{namespace}:{workspace_hash}:{payload_hash}"


_redis_unavailable_until = 0.0


def _redis_available() -> bool:
    return time.monotonic() >= _redis_unavailable_until


def _mark_redis_unavailable(exc: Exception) -> None:
    global _redis_unavailable_until
    _redis_unavailable_until = time.monotonic() + settings.governance_redis_cooldown_seconds
    log.warning("governance.redis_unavailable", error=type(exc).__name__)


@dataclass(frozen=True)
class _CacheLookup:
    status: str
    value: Any = None


class ResourceCache:
    def __init__(self, redis_factory: Callable[[], Any] | None = None) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._redis_factory = redis_factory

    def _redis(self):
        return self._redis_factory() if self._redis_factory is not None else get_redis()

    async def _get(self, key: str, namespace: str) -> _CacheLookup:
        key_hash = key.rsplit(":", 1)[-1][:12]
        if not _redis_available():
            _record("cache", action="bypass", namespace=namespace, key_hash=key_hash)
            return _CacheLookup("bypass")
        redis = self._redis()
        try:
            raw = await redis.get(key)
        except Exception as exc:
            _mark_redis_unavailable(exc)
            _record("cache", action="error", namespace=namespace, key_hash=key_hash)
            return _CacheLookup("error")
        finally:
            await redis.aclose()
        if raw is None:
            _record("cache", action="miss", namespace=namespace, key_hash=key_hash)
            return _CacheLookup("miss")
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            _record("cache", action="error", namespace=namespace, key_hash=key_hash)
            return _CacheLookup("error")
        _record("cache", action="hit", namespace=namespace, key_hash=key_hash)
        return _CacheLookup("hit", value)

    async def _set(self, key: str, namespace: str, value: Any, ttl_seconds: int) -> None:
        if not _redis_available():
            return
        key_hash = key.rsplit(":", 1)[-1][:12]
        redis = self._redis()
        try:
            await redis.set(
                key,
                json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str),
                ex=ttl_seconds,
            )
            _record(
                "cache",
                action="store",
                namespace=namespace,
                key_hash=key_hash,
                ttl_seconds=ttl_seconds,
            )
        except Exception as exc:
            _mark_redis_unavailable(exc)
            _record("cache", action="error", namespace=namespace, key_hash=key_hash)
        finally:
            await redis.aclose()

    async def get_or_compute(
        self,
        *,
        namespace: str,
        workspace_id: uuid.UUID | None,
        key_payload: Any,
        ttl_seconds: int,
        compute: Callable[[], Awaitable[T]],
        serialize: Callable[[T], Any] = lambda value: value,
        deserialize: Callable[[Any], T] = lambda value: value,
    ) -> T:
        if not settings.resource_cache_enabled or workspace_id is None or ttl_seconds <= 0:
            _record("cache", action="bypass", namespace=namespace, reason="disabled_or_unscoped")
            return await compute()
        key = cache_key(namespace, workspace_id, key_payload)
        lookup = await self._get(key, namespace)
        if lookup.status == "hit":
            return deserialize(lookup.value)
        if lookup.status in {"error", "bypass"}:
            return await compute()

        # Avoid an in-process cache stampede, then re-check Redis because the
        # first waiter may have populated it while this coroutine was queued.
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                second = await self._get(key, namespace)
                if second.status == "hit":
                    return deserialize(second.value)
                value = await compute()
                await self._set(key, namespace, serialize(value), ttl_seconds)
                return value
        finally:
            if not lock.locked():
                self._locks.pop(key, None)


resource_cache = ResourceCache()


@dataclass
class _LocalCircuit:
    state: str = "closed"
    failures: int = 0
    first_failure_ms: int = 0
    open_until_ms: int = 0
    probe_until_ms: int = 0


_BREAKER_BEFORE_LUA = """
local state = redis.call('HGET', KEYS[1], 'state')
if not state then return {'allow', 'closed'} end
if state == 'open' then
  local open_until = tonumber(redis.call('HGET', KEYS[1], 'open_until') or '0')
  if open_until > tonumber(ARGV[1]) then return {'block', 'open'} end
  redis.call('HSET', KEYS[1], 'state', 'half_open', 'probe_until', ARGV[2])
  redis.call('PEXPIRE', KEYS[1], ARGV[3])
  return {'allow', 'half_open'}
end
if state == 'half_open' then
  local probe_until = tonumber(redis.call('HGET', KEYS[1], 'probe_until') or '0')
  if probe_until > tonumber(ARGV[1]) then return {'block', 'half_open'} end
  redis.call('HSET', KEYS[1], 'probe_until', ARGV[2])
  redis.call('PEXPIRE', KEYS[1], ARGV[3])
  return {'allow', 'half_open'}
end
return {'allow', 'closed'}
"""

_BREAKER_FAILURE_LUA = """
local now = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local window = tonumber(ARGV[3])
local recovery = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
local failures = tonumber(redis.call('HGET', KEYS[1], 'failures') or '0')
local first = tonumber(redis.call('HGET', KEYS[1], 'first_failure') or '0')
if state == 'half_open' then
  failures = threshold
elseif first == 0 or now - first > window then
  failures = 0
  first = now
end
failures = failures + 1
if failures >= threshold then
  state = 'open'
  redis.call('HSET', KEYS[1], 'open_until', now + recovery)
else
  state = 'closed'
end
redis.call(
  'HSET', KEYS[1],
  'state', state,
  'failures', failures,
  'first_failure', first
)
redis.call('PEXPIRE', KEYS[1], ttl)
return {state, tostring(failures)}
"""


class CircuitBreaker:
    def __init__(
        self,
        *,
        use_redis: bool = True,
        clock: Callable[[], float] = time.time,
        failure_threshold: int | None = None,
        failure_window_seconds: int | None = None,
        recovery_seconds: int | None = None,
        half_open_timeout_seconds: int | None = None,
    ) -> None:
        self.use_redis = use_redis
        self.clock = clock
        self.failure_threshold = (
            failure_threshold
            if failure_threshold is not None
            else settings.circuit_breaker_failure_threshold
        )
        self.failure_window_seconds = (
            failure_window_seconds
            if failure_window_seconds is not None
            else settings.circuit_breaker_failure_window_seconds
        )
        self.recovery_seconds = (
            recovery_seconds
            if recovery_seconds is not None
            else settings.circuit_breaker_recovery_seconds
        )
        self.half_open_timeout_seconds = (
            half_open_timeout_seconds
            if half_open_timeout_seconds is not None
            else settings.circuit_breaker_half_open_timeout_seconds
        )
        self._local: dict[str, _LocalCircuit] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(dependency: str) -> str:
        digest = hashlib.sha256(dependency.encode()).hexdigest()
        return f"governance:circuit:v1:{digest}"

    async def _before_local(self, dependency: str, now_ms: int) -> tuple[str, str]:
        async with self._lock:
            state = self._local.setdefault(dependency, _LocalCircuit())
            if state.state == "open":
                if now_ms < state.open_until_ms:
                    return "block", "open"
                state.state = "half_open"
                state.probe_until_ms = now_ms + self.half_open_timeout_seconds * 1000
                return "allow", "half_open"
            if state.state == "half_open":
                if now_ms < state.probe_until_ms:
                    return "block", "half_open"
                state.probe_until_ms = now_ms + self.half_open_timeout_seconds * 1000
                return "allow", "half_open"
            return "allow", "closed"

    async def before_call(self, dependency: str) -> str:
        if not settings.circuit_breaker_enabled:
            return "disabled"
        now_ms = int(self.clock() * 1000)
        backend = "local"
        result: tuple[str, str] | None = None
        if self.use_redis and _redis_available():
            redis = get_redis()
            try:
                raw = await redis.eval(
                    _BREAKER_BEFORE_LUA,
                    1,
                    self._key(dependency),
                    now_ms,
                    now_ms + self.half_open_timeout_seconds * 1000,
                    (self.recovery_seconds + self.half_open_timeout_seconds) * 1000,
                )
                result = (str(raw[0]), str(raw[1]))
                backend = "redis"
            except Exception as exc:
                _mark_redis_unavailable(exc)
            finally:
                await redis.aclose()
        if result is None:
            result = await self._before_local(dependency, now_ms)
        action, state = result
        _record(
            "circuit",
            dependency=dependency,
            action=action,
            state=state,
            backend=backend,
        )
        if action == "block":
            raise CircuitOpenError(
                f"Dependency '{dependency}' is temporarily unavailable ({state} circuit)."
            )
        return state

    async def record_success(self, dependency: str) -> None:
        if not settings.circuit_breaker_enabled:
            return
        async with self._lock:
            self._local.pop(dependency, None)
        if self.use_redis and _redis_available():
            redis = get_redis()
            try:
                await redis.delete(self._key(dependency))
            except Exception as exc:
                _mark_redis_unavailable(exc)
            finally:
                await redis.aclose()

    async def _failure_local(self, dependency: str, now_ms: int) -> tuple[str, int]:
        async with self._lock:
            state = self._local.setdefault(dependency, _LocalCircuit())
            window_ms = self.failure_window_seconds * 1000
            if state.state == "half_open":
                state.failures = self.failure_threshold
            elif not state.first_failure_ms or now_ms - state.first_failure_ms > window_ms:
                state.failures = 0
                state.first_failure_ms = now_ms
            state.failures += 1
            if state.failures >= self.failure_threshold:
                state.state = "open"
                state.open_until_ms = now_ms + self.recovery_seconds * 1000
            else:
                state.state = "closed"
            return state.state, state.failures

    async def record_failure(self, dependency: str, error: Exception) -> None:
        if not settings.circuit_breaker_enabled:
            return
        now_ms = int(self.clock() * 1000)
        backend = "local"
        result: tuple[str, int] | None = None
        if self.use_redis and _redis_available():
            redis = get_redis()
            try:
                raw = await redis.eval(
                    _BREAKER_FAILURE_LUA,
                    1,
                    self._key(dependency),
                    now_ms,
                    self.failure_threshold,
                    self.failure_window_seconds * 1000,
                    self.recovery_seconds * 1000,
                    (
                        self.failure_window_seconds
                        + self.recovery_seconds
                        + self.half_open_timeout_seconds
                    )
                    * 1000,
                )
                result = (str(raw[0]), int(raw[1]))
                backend = "redis"
            except Exception as exc:
                _mark_redis_unavailable(exc)
            finally:
                await redis.aclose()
        if result is None:
            result = await self._failure_local(dependency, now_ms)
        state, failures = result
        _record(
            "circuit",
            dependency=dependency,
            action="failure",
            state=state,
            failures=failures,
            error_type=type(error).__name__,
            backend=backend,
        )

    async def call(self, dependency: str, operation: Callable[[], Awaitable[T]]) -> T:
        await self.before_call(dependency)
        try:
            result = await operation()
        except (CircuitOpenError, ResourceBudgetExceeded):
            raise
        except Exception as exc:
            await self.record_failure(dependency, exc)
            raise
        await self.record_success(dependency)
        return result


circuit_breaker = CircuitBreaker()
