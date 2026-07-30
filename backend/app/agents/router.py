"""Intent router: the single entry that decides what the user wants, so the app
dispatches on intent instead of making the user pick a mode.

Phase 1 tells web search apart from ordinary grounded Q&A. quiz and plan intents
join here as those move into chat.

Red line (docs/02-features.md 2.9): web only ever runs on an explicit request.
Here that request is the user's own words ("go online / 上网查"), classified
conservatively — the default is always qa, and this never fires as a silent
fallback when retrieval comes up short.
"""

from typing import Literal

from langchain_core.messages import HumanMessage

from app.services import usage
from app.services.providers import IntelligenceTier, chat_model

Intent = Literal["qa", "web", "quiz", "plan"]

CLASSIFY_PROMPT = """{context}The user's latest message is:
"{question}"

Reply with exactly one word for what they want NOW:
- web  — the latest message explicitly asks to go online / search the internet
  ("上网查", "联网", "搜一下", "search the web", "google it"). This must be asked in
  the latest message itself; never infer it from earlier turns.
- quiz — they want practice questions / a test ("出几道题", "考考我", "再来几道", "quiz me").
- plan — they want to make or change a study plan or schedule ("制定学习计划", "调整计划",
  "把第二阶段改详细点"). A follow-up about a plan just discussed also counts, e.g.
  "后面的内容没了，补全一下", "太难了简单点", "去掉最后一阶段".
- qa   — anything else: answer a question from their own study material.

Use the recent conversation only to understand a follow-up (a quiz / plan / qa
continuation); never to turn a non-web message into web.

When unsure, answer qa."""


def _context(history: list[tuple[str, str]] | None) -> str:
    if not history:
        return ""
    recent = history[-4:]
    lines = "\n".join(f"{role}: {content[:200]}" for role, content in recent)
    return f"Recent conversation:\n{lines}\n\n"


async def classify_intent(question: str, history: list[tuple[str, str]] | None = None) -> Intent:
    prompt = CLASSIFY_PROMPT.format(context=_context(history), question=question)
    reply = await chat_model(IntelligenceTier.FAST).ainvoke([HumanMessage(prompt)])
    usage.record_message("router", reply)
    verdict = reply.text.strip().lower()
    if "web" in verdict:
        return "web"
    if "quiz" in verdict:
        return "quiz"
    if "plan" in verdict:
        return "plan"
    return "qa"
