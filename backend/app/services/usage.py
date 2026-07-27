"""Token and cost accounting for one turn.

A single question fans out into several model calls — grade, rerank, generate,
plus the query embedding — so the number a user cares about is the total for
the turn, not per call. Calls collect into a context-local ledger rather than
being threaded through every signature, because the LangGraph nodes in between
have no business carrying an accounting object.

Prices are configuration: they change, and they differ per account. An unpriced
model still reports tokens, with cost left unknown rather than guessed.
"""

import contextvars
from dataclasses import asdict, dataclass, field

import structlog
import tiktoken
from langchain_core.messages import BaseMessage

from app.core.config import settings

log = structlog.get_logger()

_ledger: contextvars.ContextVar["Usage | None"] = contextvars.ContextVar("usage", default=None)


@dataclass
class Call:
    step: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


@dataclass
class Usage:
    calls: list[Call] = field(default_factory=list)

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def cost_usd(self) -> float | None:
        priced = [c.cost_usd for c in self.calls if c.cost_usd is not None]
        return round(sum(priced), 6) if priced else None

    def as_payload(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "cost_usd": self.cost_usd,
            "priced": all(c.cost_usd is not None for c in self.calls) if self.calls else False,
            "calls": [asdict(c) for c in self.calls],
        }


def start() -> Usage:
    usage = Usage()
    _ledger.set(usage)
    return usage


def current() -> Usage | None:
    return _ledger.get()


def price(model: str, input_tokens: int, output_tokens: int) -> float | None:
    rate = settings.model_prices.get(model)
    if rate is None:
        return None
    return (input_tokens * rate[0] + output_tokens * rate[1]) / 1_000_000


def record(step: str, model: str, input_tokens: int, output_tokens: int = 0) -> None:
    usage = current()
    if usage is None:
        return
    if model not in settings.model_prices:
        log.info("usage.unpriced_model", model=model, step=step)
    usage.calls.append(
        Call(
            step=step,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=price(model, input_tokens, output_tokens),
        )
    )


def record_flat(step: str, model: str, cost_usd: float | None) -> None:
    """For services billed per call rather than per token, such as rerankers."""
    usage = current()
    if usage is None:
        return
    usage.calls.append(
        Call(step=step, model=model, input_tokens=0, output_tokens=0, cost_usd=cost_usd)
    )


def record_embedding(step: str, text: str) -> None:
    """Embedding APIs bill for input but report no counts, so count locally."""
    try:
        encoding = tiktoken.encoding_for_model(settings.embedding_model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    record(step, settings.embedding_model, len(encoding.encode(text)))


def record_message(step: str, message: BaseMessage) -> None:
    """Read the provider's own counts off a reply, when it reported them."""
    meta = getattr(message, "usage_metadata", None)
    if not meta:
        log.info("usage.no_metadata", step=step)
        return
    model = (message.response_metadata or {}).get("model_name") or settings.llm_model
    record(step, model, meta.get("input_tokens", 0), meta.get("output_tokens", 0))
