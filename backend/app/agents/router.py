"""Intent router for the chat-first learning experience.

Every learner action starts in chat. The router returns a confidence-bearing
decision so an ambiguous request can be clarified by the learner instead of
being silently sent to the wrong agent.
"""

import json
import re
import uuid
from dataclasses import dataclass
from typing import Literal, cast

from langchain_core.messages import HumanMessage

from app.agents.task_dag import AgentTask, TaskAgent
from app.core.config import settings
from app.prompts.registry import render_prompt
from app.services import usage
from app.services.agent_security import authorize_tool
from app.services.providers import IntelligenceTier, chat_model, model_trace
from app.services.resource_governance import resource_cache

Intent = Literal["qa", "web", "quiz", "test", "review", "progress", "plan", "explain", "lecture"]
INTENTS: tuple[Intent, ...] = (
    "qa",
    "web",
    "quiz",
    "test",
    "review",
    "progress",
    "plan",
    "explain",
    "lecture",
)
ROUTER_CONFIDENCE_THRESHOLD = 0.68
WEB_REQUEST_PATTERNS = (
    re.compile(r"(?:上网|联网|互联网|网上|网页|网络).{0,10}(?:查|搜|找|检索)"),
    re.compile(r"(?:查|搜|找|检索).{0,10}(?:互联网|网上|网页|网络)"),
    re.compile(r"\b(?:search|browse|look up|google).{0,20}\b(?:web|internet|online)\b", re.I),
    re.compile(r"\b(?:web|internet|online).{0,20}\b(?:search|browse|look up)\b", re.I),
    re.compile(r"\bgoogle\s+(?:it|this|that)\b", re.I),
)


def explicit_web_request(question: str) -> bool:
    """Code-level consent gate: a Router decision cannot itself authorize web I/O."""
    return any(pattern.search(question) for pattern in WEB_REQUEST_PATTERNS)


def filter_authorized_tasks(
    tasks: tuple["AgentTask", ...],
    question: str,
    *,
    web_search_enabled: bool,
    explicit_web_action: bool = False,
) -> tuple["AgentTask", ...]:
    """Remove Web tasks unless configuration and user consent both allow I/O."""
    web_authorized = explicit_web_action or explicit_web_request(question)
    web_decision = authorize_tool(
        "web_search",
        deployment_enabled=web_search_enabled,
        user_authorized=web_authorized,
    )
    retained = tuple(task for task in tasks if task.agent != "web" or web_decision.allowed)
    retained_ids = {task.id for task in retained if task.id}
    knowledge_count = sum(task.agent in ("qa", "web") for task in retained)
    return tuple(
        task
        for task in retained
        if task.agent != "answer"
        or (
            knowledge_count >= 2
            and (
                not task.depends_on
                or all(dependency in retained_ids for dependency in task.depends_on)
            )
        )
    )


@dataclass(frozen=True)
class IntentDecision:
    intent: Intent | None
    confidence: float
    alternatives: tuple[Intent, ...] = ()
    reason: str = ""
    tasks: tuple[AgentTask, ...] = ()

    @property
    def needs_clarification(self) -> bool:
        return (
            self.intent is None and not self.tasks
        ) or self.confidence < ROUTER_CONFIDENCE_THRESHOLD


INTENT_OPTIONS: dict[Intent, dict[str, str]] = {
    "qa": {"label": "直接回答", "description": "根据学习资料简洁回答这个问题"},
    "web": {"label": "联网查找", "description": "搜索互联网后回答"},
    "quiz": {"label": "随堂练习", "description": "在聊天中出题，作答后立即看解析"},
    "test": {"label": "正式测试", "description": "开始计时测试，提交后统一评分"},
    "review": {"label": "复习错题", "description": "练习当前到期的错题"},
    "progress": {"label": "查看掌握度", "description": "总结当前学习进度和薄弱知识点"},
    "plan": {"label": "调整计划", "description": "创建或修改学习计划"},
    "explain": {"label": "详细讲解", "description": "系统讲解并展示知识关系"},
    "lecture": {"label": "互动讲课", "description": "分节讲解、检验理解并保留进度"},
}

CLASSIFY_PROMPT = """{context}The user's latest message is:
"{question}"

Build the smallest agent plan that fulfils what the learner wants NOW. Return JSON only:
{{"intent":"primary intent","confidence":0.0,
  "tasks":[{{"id":"stable_snake_case_id",
             "agent":"qa|web|quiz|test|review|progress|plan|explain|lecture|answer",
             "query":"standalone subtask for that agent",
             "depends_on":["upstream_task_id"]}}],
  "alternatives":["..."],"reason":"brief reason"}}

- web: the latest message explicitly asks to go online/search the internet. Never infer web.
- quiz: low-stakes practice with immediate feedback ("出几道练习题", "quiz me").
- test: a formal or timed assessment whose answers are submitted together ("测试我", "模拟考试").
- review: review wrong or currently due questions ("复习错题", "今天要复习什么").
- progress: inspect mastery, learning progress, strengths, or weak topics.
- plan: create, continue, or change a study plan or schedule.
- explain: a systematic, detailed, step-by-step lesson or knowledge map.
- lecture: start, continue, pause or resume a multi-turn interactive lesson with
  section-by-section teaching and understanding checks ("给我讲一节课", "继续讲课").
- qa: a normal question answered from the learner's material.

Use one task for a simple request. Use multiple tasks when the request explicitly
needs different sources or capabilities. In particular, split a request that asks
to search the internet AND inspect the learner's material into a focused `web` task
and a focused `qa` task, then add one `answer` synthesis task that depends on both.
The `web` and `qa` tasks have no dependencies and may run in parallel. Rewrite every
task query so it is independently meaningful and resolves pronouns from the original
request. Do not collapse such a request into only `web` or only `qa`. Multi-task
knowledge gathering currently supports `web` and `qa`; keep action intents such as
quiz/test/plan as a single task with no dependencies. IDs must be unique and every
dependency must reference an earlier task ID.

Use recent conversation only to understand follow-ups. When multiple actions
are alternative interpretations rather than requested subtasks, lower confidence
below 0.68 and list the best 2-3 alternatives instead of running all of them.
Do not invent ambiguity for clear requests."""


def _context(history: list[tuple[str, str]] | None) -> str:
    if not history:
        return ""
    recent = history[-4:]
    lines = "\n".join(f"{role}: {content[:200]}" for role, content in recent)
    return f"Recent conversation:\n{lines}\n\n"


def _valid_intent(value: object) -> Intent | None:
    normalized = str(value or "").strip().lower()
    return cast(Intent, normalized) if normalized in INTENTS else None


def _valid_task_agent(value: object) -> TaskAgent | None:
    normalized = str(value or "").strip().lower()
    if normalized in (*INTENTS, "answer"):
        return cast(TaskAgent, normalized)
    return None


def parse_decision(text: str) -> IntentDecision:
    """Parse structured output, retaining one-word compatibility for providers
    that ignore the JSON instruction and for deterministic test doubles."""
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            payload = json.loads(match.group(0))
            intent = _valid_intent(payload.get("intent"))
            tasks = tuple(
                AgentTask(
                    agent=agent,
                    query=query[:1000],
                    id=str(item.get("id") or "").strip()[:80],
                    depends_on=tuple(
                        str(dependency).strip()[:80]
                        for dependency in item.get("depends_on", [])[:8]
                        if str(dependency).strip()
                    )
                    if isinstance(item.get("depends_on", []), list)
                    else (),
                )
                for item in payload.get("tasks", [])[:8]
                if isinstance(item, dict)
                and (agent := _valid_task_agent(item.get("agent"))) is not None
                and (query := str(item.get("query") or "").strip())
            )
            worker_tasks = tuple(task for task in tasks if task.agent != "answer")
            # Action agents retain their dedicated transactional flows.
            if len(tasks) > 1 and any(task.agent not in ("qa", "web", "answer") for task in tasks):
                tasks = ()
                worker_tasks = ()
            if intent is None and worker_tasks:
                intent = cast(Intent, worker_tasks[0].agent)
            confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))
            alternatives = tuple(
                alternative
                for item in payload.get("alternatives", [])[:3]
                if (alternative := _valid_intent(item)) is not None and alternative != intent
            )
            return IntentDecision(
                intent=intent,
                confidence=confidence,
                alternatives=alternatives,
                reason=str(payload.get("reason") or ""),
                tasks=tasks,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    verdict = text.strip().lower()
    for intent in INTENTS:
        if re.search(rf"\b{intent}\b", verdict):
            return IntentDecision(intent=intent, confidence=0.95, reason="legacy classifier output")
    return IntentDecision(intent=None, confidence=0.0, alternatives=("qa", "explain", "quiz"))


async def route_intent(
    question: str,
    history: list[tuple[str, str]] | None = None,
    workspace_id: uuid.UUID | None = None,
) -> IntentDecision:
    prompt = await render_prompt(
        "router.classify",
        {"context": _context(history), "question": question},
        workspace_id=workspace_id,
        step="router",
    )

    async def classify() -> str:
        reply = await chat_model(IntelligenceTier.FAST).ainvoke([HumanMessage(prompt.text)])
        usage.record_message("router", reply)
        return reply.text

    text = await resource_cache.get_or_compute(
        namespace="router",
        workspace_id=workspace_id,
        key_payload={
            "question": question,
            "history": history or [],
            "prompt": prompt.prompt.metadata(),
            "model": model_trace(IntelligenceTier.FAST),
        },
        ttl_seconds=settings.router_cache_ttl_seconds,
        compute=classify,
    )
    return parse_decision(text)


async def classify_intent(question: str, history: list[tuple[str, str]] | None = None) -> Intent:
    """Compatibility helper for callers that only need the most likely intent."""
    decision = await route_intent(question, history)
    return decision.intent or "qa"


def clarification_options(
    decision: IntentDecision, *, web_search_enabled: bool
) -> list[dict[str, str]]:
    candidates = list(decision.alternatives)
    if decision.intent is not None:
        candidates.insert(0, decision.intent)
    if len(candidates) < 2:
        fallbacks: dict[Intent, tuple[Intent, ...]] = {
            "qa": ("explain", "quiz"),
            "explain": ("qa", "quiz"),
            "quiz": ("test", "explain"),
            "test": ("quiz", "review"),
            "review": ("progress", "quiz"),
            "progress": ("review", "plan"),
            "plan": ("progress", "explain"),
            "lecture": ("explain", "quiz"),
            "web": ("qa", "explain"),
        }
        candidates.extend(fallbacks.get(decision.intent or "qa", ("qa", "explain")))

    unique: list[Intent] = []
    for intent in candidates:
        if intent == "web" and not web_search_enabled:
            continue
        if intent not in unique:
            unique.append(intent)
    return [{"intent": intent, **INTENT_OPTIONS[intent]} for intent in unique[:3]]
