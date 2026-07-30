"""Portable, bounded recovery for model responses that must be JSON.

Provider-native schemas are not available in every configured backend.  This
module therefore keeps parsing and recovery consistent across agents:

1. extract the first complete JSON object from surrounding prose/fences;
2. repair common local formatting mistakes without another model call;
3. validate the payload with the agent's domain parser;
4. make one bounded model repair call and validate again.

It deliberately never guesses a domain value.  Callers decide whether a safe
fallback exists or whether workflow state must remain unchanged for a retry.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.services import usage


class StructuredOutputError(ValueError):
    """A model response remained invalid after the bounded repair pass."""

    def __init__(self, message: str, *, raw_text: str = "") -> None:
        super().__init__(message)
        self.raw_text = raw_text


@dataclass(frozen=True)
class ParsedObject:
    value: dict
    recovery_method: str | None = None


@dataclass(frozen=True)
class StructuredResult:
    value: Any
    recovered: bool
    recovery_method: str | None = None


def _object_candidates(text: str):
    """Yield balanced objects while respecting braces inside JSON strings."""

    for start, character in enumerate(text):
        if character != "{":
            continue
        depth = 0
        quote: str | None = None
        escaped = False
        for end in range(start, len(text)):
            current = text[end]
            if quote is not None:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    quote = None
                continue
            if current in ('"', "'"):
                quote = current
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : end + 1]
                    break


def _strict_object(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    for candidate in _object_candidates(text):
        try:
            value, _end = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_json_object(text: str) -> ParsedObject:
    """Parse a JSON object and recover common serialization-only mistakes."""

    stripped = text.strip()
    strict = _strict_object(stripped)
    if strict is not None:
        method = None if stripped.startswith("{") else "extracted_json"
        return ParsedObject(strict, method)

    # A dangling comma is a frequent streaming/model formatting error and is
    # unambiguous to repair immediately before a closing delimiter.
    without_trailing_commas = re.sub(r",\s*([}\]])", r"\1", stripped)
    strict = _strict_object(without_trailing_commas)
    if strict is not None:
        return ParsedObject(strict, "removed_trailing_commas")

    # Models occasionally emit a Python dict (single quotes / True / None).
    # literal_eval is data-only; round-tripping through JSON also rejects types
    # that are not valid JSON values.
    for candidate in _object_candidates(stripped):
        try:
            value = ast.literal_eval(candidate.strip().strip("`").strip())
            json.dumps(value)
        except (SyntaxError, ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return ParsedObject(value, "python_literal")

    raise StructuredOutputError("Model returned invalid JSON", raw_text=text)


def _validated(text: str, parser: Callable[[str], Any]) -> tuple[Any, str | None]:
    parsed = parse_json_object(text)
    canonical = json.dumps(parsed.value, ensure_ascii=False)
    try:
        return parser(canonical), parsed.recovery_method
    except (KeyError, TypeError, ValueError) as exc:
        raise StructuredOutputError(
            "Model returned JSON that did not match the required schema", raw_text=text
        ) from exc


async def invoke_structured(
    *,
    model,
    messages: list,
    step: str,
    schema: str,
    parser: Callable[[str], Any],
    timeout_seconds: int = 90,
) -> StructuredResult:
    """Invoke a model, validate JSON, and make at most one repair call."""

    async def invoke(call_messages: list, call_step: str):
        try:
            reply = await asyncio.wait_for(model.ainvoke(call_messages), timeout=timeout_seconds)
        except TimeoutError as exc:
            raise TimeoutError(
                f"Model timed out after {timeout_seconds} seconds during {call_step}"
            ) from exc
        usage.record_message(call_step, reply)
        return reply

    first = await invoke(messages, step)
    try:
        value, local_method = _validated(first.text, parser)
        return StructuredResult(
            value=value,
            recovered=local_method is not None,
            recovery_method=local_method,
        )
    except StructuredOutputError:
        pass

    repair = await invoke(
        [
            SystemMessage(
                "Convert the candidate into valid JSON matching the required schema. "
                "Preserve its meaning, output JSON only, and do not add facts.\n"
                f"Required schema: {schema}"
            ),
            HumanMessage(f"Candidate output:\n{first.text}"),
        ],
        f"{step}_repair",
    )
    try:
        value, local_method = _validated(repair.text, parser)
    except StructuredOutputError as exc:
        raise StructuredOutputError(
            f"{step} returned invalid structured output after one repair attempt",
            raw_text=first.text,
        ) from exc
    return StructuredResult(
        value=value,
        recovered=True,
        recovery_method=local_method or "model_repair",
    )
