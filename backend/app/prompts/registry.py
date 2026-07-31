"""Versioned prompt resolution, rendering and per-run usage capture.

Code-owned prompts are the availability fallback; a workspace may activate one
immutable override per key. Replay pins are stricter: key, source, version and
content hash must all resolve or replay fails closed. Context variables isolate
prompt usage and pins per async turn, while the short process cache avoids a
database read on every model call.
"""

from __future__ import annotations

import contextvars
import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from string import Formatter
from time import monotonic
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.prompt import PromptDefinition, PromptVersion


class PromptRegistryError(ValueError):
    """Prompt identity, contract, rendering or replay resolution is invalid."""


@dataclass(frozen=True)
class BuiltinPrompt:
    key: str
    version: int
    description: str
    template: str

    @property
    def variables(self) -> tuple[str, ...]:
        return template_variables(self.template)

    @property
    def content_hash(self) -> str:
        return prompt_hash(self.template)


@dataclass(frozen=True)
class ResolvedPrompt:
    """Exact immutable prompt identity selected for one invocation."""

    key: str
    version: int
    description: str
    template: str
    variables: tuple[str, ...]
    content_hash: str
    source: str

    def metadata(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "version": self.version,
            "content_hash": self.content_hash,
            "source": self.source,
        }


@dataclass(frozen=True)
class RenderedPrompt:
    text: str
    prompt: ResolvedPrompt


@dataclass(frozen=True)
class PromptUse:
    step: str
    key: str
    version: int
    content_hash: str
    source: str


@dataclass
class PromptTrace:
    """Per-turn prompt manifest later attached to usage, Eval and AgentRun rows."""

    uses: list[PromptUse] = field(default_factory=list)

    def record(self, step: str, prompt: ResolvedPrompt) -> None:
        self.uses.append(PromptUse(step=step, **prompt.metadata()))

    def for_step(self, step: str) -> dict[str, Any] | None:
        use = next((item for item in reversed(self.uses) if item.step == step), None)
        return asdict(use) if use is not None else None

    def as_payload(self) -> dict[str, Any]:
        manifest: dict[str, dict[str, Any]] = {}
        for use in self.uses:
            manifest[use.key] = {
                "key": use.key,
                "version": use.version,
                "content_hash": use.content_hash,
                "source": use.source,
            }
        return {
            "uses": [asdict(use) for use in self.uses],
            "manifest": [manifest[key] for key in sorted(manifest)],
        }


_trace: contextvars.ContextVar[PromptTrace | None] = contextvars.ContextVar(
    "prompt_trace", default=None
)
_pins: contextvars.ContextVar[dict[str, dict[str, Any]] | None] = contextvars.ContextVar(
    "prompt_version_pins", default=None
)
_builtins: dict[str, BuiltinPrompt] | None = None
_cache: dict[tuple[uuid.UUID, str], tuple[float, ResolvedPrompt]] = {}


def prompt_hash(template: str) -> str:
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def template_variables(template: str) -> tuple[str, ...]:
    variables: list[str] = []
    try:
        parsed = Formatter().parse(template)
        for _literal, field_name, _format_spec, _conversion in parsed:
            if field_name is None:
                continue
            if not field_name or any(character in field_name for character in ".["):
                raise PromptRegistryError("Prompt variables must be named top-level fields")
            if field_name not in variables:
                variables.append(field_name)
    except ValueError as exc:
        raise PromptRegistryError(f"Prompt template has invalid braces: {exc}") from exc
    return tuple(variables)


def _load_builtins() -> dict[str, BuiltinPrompt]:
    global _builtins
    if _builtins is None:
        from app.prompts.defaults import default_prompts

        items = default_prompts()
        loaded = {item.key: item for item in items}
        if len(loaded) != len(items):
            raise PromptRegistryError("Built-in prompt keys must be unique")
        for item in loaded.values():
            template_variables(item.template)
        _builtins = loaded
    return _builtins


def list_builtin_prompts() -> tuple[BuiltinPrompt, ...]:
    return tuple(sorted(_load_builtins().values(), key=lambda item: item.key))


def get_builtin_prompt(key: str) -> BuiltinPrompt:
    try:
        return _load_builtins()[key]
    except KeyError as exc:
        raise PromptRegistryError(f"Unknown prompt key: {key}") from exc


def start_prompt_trace() -> PromptTrace:
    trace = PromptTrace()
    _trace.set(trace)
    return trace


def current_prompt_trace() -> PromptTrace | None:
    return _trace.get()


def prompt_for_step(step: str) -> dict[str, Any] | None:
    trace = current_prompt_trace()
    return trace.for_step(step) if trace is not None else None


def set_prompt_pins(manifest: list[dict[str, Any]]):
    return _pins.set(
        {
            str(item["key"]): dict(item)
            for item in manifest
            if isinstance(item, dict) and item.get("key")
        }
    )


def reset_prompt_pins(token) -> None:
    _pins.reset(token)


def clear_prompt_cache(workspace_id: uuid.UUID | None = None) -> None:
    if workspace_id is None:
        _cache.clear()
        return
    for key in [item for item in _cache if item[0] == workspace_id]:
        _cache.pop(key, None)


def _builtin_resolved(item: BuiltinPrompt) -> ResolvedPrompt:
    return ResolvedPrompt(
        key=item.key,
        version=item.version,
        description=item.description,
        template=item.template,
        variables=item.variables,
        content_hash=item.content_hash,
        source="builtin",
    )


async def _workspace_version(
    workspace_id: uuid.UUID,
    key: str,
    *,
    version: int | None = None,
    content_hash: str | None = None,
) -> ResolvedPrompt | None:
    async with AsyncSessionLocal() as db:
        query = (
            select(PromptVersion)
            .join(PromptDefinition)
            .options(selectinload(PromptVersion.definition))
            .where(
                PromptDefinition.workspace_id == workspace_id,
                PromptDefinition.key == key,
            )
        )
        if version is None:
            query = query.where(PromptVersion.status == "active")
        else:
            query = query.where(PromptVersion.version == version)
        if content_hash:
            query = query.where(PromptVersion.content_hash == content_hash)
        row = await db.scalar(query)
    if row is None:
        return None
    return ResolvedPrompt(
        key=key,
        version=row.version,
        description=row.definition.description,
        template=row.template,
        variables=tuple(row.variables),
        content_hash=row.content_hash,
        source="workspace",
    )


async def resolve_prompt(
    key: str,
    *,
    workspace_id: uuid.UUID | None = None,
) -> ResolvedPrompt:
    builtin = get_builtin_prompt(key)
    pin = (_pins.get() or {}).get(key)
    if pin:
        # Replay must never fall back to today's active prompt: that would create
        # a plausible-looking run that did not reproduce the original behavior.
        source = pin.get("source")
        version = int(pin.get("version", 0))
        content_hash = str(pin.get("content_hash") or "")
        if (
            source == "builtin"
            and version == builtin.version
            and content_hash == builtin.content_hash
        ):
            return _builtin_resolved(builtin)
        if workspace_id is not None and source == "workspace":
            resolved = await _workspace_version(
                workspace_id,
                key,
                version=version,
                content_hash=content_hash,
            )
            if resolved is not None:
                return resolved
        raise PromptRegistryError(
            f"Pinned prompt {key} v{version} ({content_hash[:12]}) is unavailable"
        )

    if workspace_id is None:
        return _builtin_resolved(builtin)
    cache_key = (workspace_id, key)
    cached = _cache.get(cache_key)
    if cached and cached[0] > monotonic():
        return cached[1]
    resolved = await _workspace_version(workspace_id, key)
    result = resolved or _builtin_resolved(builtin)
    # Activation clears this process immediately; other workers converge via TTL.
    _cache[cache_key] = (
        monotonic() + settings.prompt_cache_ttl_seconds,
        result,
    )
    return result


async def render_prompt(
    key: str,
    variables: dict[str, Any],
    *,
    workspace_id: uuid.UUID | None = None,
    step: str | None = None,
) -> RenderedPrompt:
    prompt = await resolve_prompt(key, workspace_id=workspace_id)
    supplied = set(variables)
    required = set(prompt.variables)
    if supplied != required:
        missing = sorted(required - supplied)
        extra = sorted(supplied - required)
        raise PromptRegistryError(
            f"Prompt {key} variables do not match; missing={missing}, extra={extra}"
        )
    try:
        text = prompt.template.format(**variables)
    except (KeyError, ValueError) as exc:
        raise PromptRegistryError(f"Prompt {key} could not be rendered: {exc}") from exc
    trace = current_prompt_trace()
    if trace is not None:
        trace.record(step or key, prompt)
    return RenderedPrompt(text=text, prompt=prompt)


async def active_prompt_manifest(
    workspace_id: uuid.UUID | None,
) -> list[dict[str, Any]]:
    prompts = {item.key: _builtin_resolved(item) for item in list_builtin_prompts()}
    if workspace_id is not None:
        async with AsyncSessionLocal() as db:
            rows = list(
                await db.scalars(
                    select(PromptVersion)
                    .join(PromptDefinition)
                    .options(selectinload(PromptVersion.definition))
                    .where(
                        PromptDefinition.workspace_id == workspace_id,
                        PromptVersion.status == "active",
                    )
                )
            )
        for row in rows:
            prompts[row.definition.key] = ResolvedPrompt(
                key=row.definition.key,
                version=row.version,
                description=row.definition.description,
                template=row.template,
                variables=tuple(row.variables),
                content_hash=row.content_hash,
                source="workspace",
            )
    return [prompts[key].metadata() for key in sorted(prompts)]
