"""Intent router for the chat-first learning experience.

Every learner action starts in chat. The router returns a confidence-bearing
decision so an ambiguous request can be clarified by the learner instead of
being silently sent to the wrong agent.
"""

import json
import re
from dataclasses import dataclass
from typing import Literal, cast

from langchain_core.messages import HumanMessage

from app.services import usage
from app.services.providers import IntelligenceTier, chat_model

Intent = Literal["qa", "web", "quiz", "test", "review", "progress", "plan", "explain"]
INTENTS: tuple[Intent, ...] = (
    "qa",
    "web",
    "quiz",
    "test",
    "review",
    "progress",
    "plan",
    "explain",
)
ROUTER_CONFIDENCE_THRESHOLD = 0.68


@dataclass(frozen=True)
class IntentDecision:
    intent: Intent | None
    confidence: float
    alternatives: tuple[Intent, ...] = ()
    reason: str = ""

    @property
    def needs_clarification(self) -> bool:
        return self.intent is None or self.confidence < ROUTER_CONFIDENCE_THRESHOLD


INTENT_OPTIONS: dict[Intent, dict[str, str]] = {
    "qa": {"label": "直接回答", "description": "根据学习资料简洁回答这个问题"},
    "web": {"label": "联网查找", "description": "搜索互联网后回答"},
    "quiz": {"label": "随堂练习", "description": "在聊天中出题，作答后立即看解析"},
    "test": {"label": "正式测试", "description": "开始计时测试，提交后统一评分"},
    "review": {"label": "复习错题", "description": "练习当前到期的错题"},
    "progress": {"label": "查看掌握度", "description": "总结当前学习进度和薄弱知识点"},
    "plan": {"label": "调整计划", "description": "创建或修改学习计划"},
    "explain": {"label": "详细讲解", "description": "系统讲解并展示知识关系"},
}

CLASSIFY_PROMPT = """{context}The user's latest message is:
"{question}"

Classify what the learner wants to do NOW. Return JSON only:
{{"intent":"qa|web|quiz|test|review|progress|plan|explain","confidence":0.0,
  "alternatives":["..."],"reason":"brief reason"}}

- web: the latest message explicitly asks to go online/search the internet. Never infer web.
- quiz: low-stakes practice with immediate feedback ("出几道练习题", "quiz me").
- test: a formal or timed assessment whose answers are submitted together ("测试我", "模拟考试").
- review: review wrong or currently due questions ("复习错题", "今天要复习什么").
- progress: inspect mastery, learning progress, strengths, or weak topics.
- plan: create, continue, or change a study plan or schedule.
- explain: a systematic, detailed, step-by-step lesson or knowledge map.
- qa: a normal question answered from the learner's material.

Use recent conversation only to understand follow-ups. When multiple actions
are plausible, lower confidence below 0.68 and list the best 2-3 alternatives.
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


def parse_decision(text: str) -> IntentDecision:
    """Parse structured output, retaining one-word compatibility for providers
    that ignore the JSON instruction and for deterministic test doubles."""
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            payload = json.loads(match.group(0))
            intent = _valid_intent(payload.get("intent"))
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
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    verdict = text.strip().lower()
    for intent in INTENTS:
        if re.search(rf"\b{intent}\b", verdict):
            return IntentDecision(intent=intent, confidence=0.95, reason="legacy classifier output")
    return IntentDecision(intent=None, confidence=0.0, alternatives=("qa", "explain", "quiz"))


async def route_intent(
    question: str, history: list[tuple[str, str]] | None = None
) -> IntentDecision:
    prompt = CLASSIFY_PROMPT.format(context=_context(history), question=question)
    reply = await chat_model(IntelligenceTier.FAST).ainvoke([HumanMessage(prompt)])
    usage.record_message("router", reply)
    return parse_decision(reply.text)


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
