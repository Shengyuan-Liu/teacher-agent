"""Planner graph: load_context -> draft.

Takes the workspace outline plus the user's goal and time budget, and drafts
ordered stages. Persistence stays in the service layer.
"""

import json
import re
import uuid
from datetime import date
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.agents.outline import ensure_outline
from app.services import usage
from app.services.providers import IntelligenceTier, chat_model

DRAFT_PROMPT = """You are planning a course of self-study.

Topic outline of the material (with prerequisite ids):
{outline}

Learner's goal: {goal}
Time available: {daily_minutes} minutes per day{deadline_line}

Draft a staged study plan as JSON:
{{"stages": [{{"title": "...", "description": "...", "topics": ["topic title", ...],
"activities": ["read", "quiz", "chat"], "estimated_minutes": 120}}]}}

Rules:
- 3 to 8 stages. The outline array is in authoritative source/course order;
  keep selected topics in that order and respect `depends_on`.
- If the learner asks for an N-day schedule with work for each day, return
  exactly N stages, one per day, even when N is outside the default range.
- Copy every value in `topics` exactly from an outline topic title. Do not
  invent generic course topics that are absent from the outline.
- Each stage names the outline topics it covers and roughly how many minutes it
  needs in total; keep stages sized so daily progress is realistic.
- `description` says what to do and what "done" looks like, in 2-3 sentences.
- `activities` picks from: read, chat, quiz.
- Use the learner's language as inferred from the goal text; otherwise the
  material's language.
- Output only the JSON object."""


CHAT_PLAN_PROMPT = """You are helping a learner build and refine their study plan \
through conversation.

Topic outline of the material (with prerequisite ids):
{outline}

Current plan (empty if there is none yet):
{current}

{convo}The learner just said: {request}

Return the FULL updated plan as JSON:
{{"stages": [{{"title": "...", "description": "...", "topics": ["topic title", ...],
"activities": ["read", "quiz", "chat"], "estimated_minutes": 120}}]}}

Rules:
- If there is no current plan, create one from the learner's request and the outline.
- If there is a current plan, actually apply the learner's change and return the
  COMPLETE updated list of stages. Fully honour what they ask — restructure,
  merge, split, reorder, add or remove stages as needed. Do not just return the
  existing plan unchanged.
- The learner's completed stages are shown for context (so you can build on their
  progress); this is not a reason to leave the plan as-is when they asked to change it.
- Do not put status words like "已完成"/"done" into the titles; titles name the topic only.
- Default to 3-8 stages, but if the learner asks for a specific number of stages,
  produce exactly that many. The outline array is in authoritative source/course
  order: insert additions at their proper source position and never move a later
  topic before an earlier prerequisite.
- If the learner asks for an N-day schedule with content for every day (for
  example, "10 days" and "each day"), return exactly N stages, one per day.
- Copy every value in `topics` exactly from an outline topic title. Do not
  invent generic course topics that are absent from the outline.
- `description` says what to do and what "done" looks like, in 2-3 sentences.
- `activities` picks from: read, chat, quiz.
- Write in the same language as the learner's request.
- Output only the JSON object."""


async def revise_plan(
    workspace_id: uuid.UUID,
    request: str,
    current: str,
    history: list[tuple[str, str]] | None = None,
    daily_minutes: int = 60,
    deadline: date | None = None,
) -> list[dict]:
    """Create or edit a study plan from a natural-language request. `current` is
    the existing plan rendered for the prompt (empty when there is none); recent
    conversation lets follow-up edits resolve references to what was just said."""
    outline = await ensure_outline(workspace_id)
    topics = _topics_for_prompt(outline)
    convo = ""
    if history:
        recent = "\n".join(f"{role}: {content[:300]}" for role, content in history[-4:])
        convo = f"Recent conversation:\n{recent}\n\n"
    reply = await chat_model(IntelligenceTier.SMART).ainvoke(
        [
            HumanMessage(
                CHAT_PLAN_PROMPT.format(
                    outline=topics, current=current or "(none)", convo=convo, request=request
                )
            )
        ]
    )
    usage.record_message("plan_revise", reply)
    return finalise_stages(parse_stages(reply.text), outline["topics"], daily_minutes, deadline)


class PlanState(TypedDict):
    workspace_id: str
    goal: str
    daily_minutes: int
    deadline: str | None
    outline: dict
    stages: list[dict]


async def load_context(state: PlanState) -> dict:
    outline = await ensure_outline(uuid.UUID(state["workspace_id"]))
    return {"outline": outline}


async def draft(state: PlanState) -> dict:
    topics = _topics_for_prompt(state["outline"])
    deadline_line = f"\nDeadline: {state['deadline']}" if state["deadline"] else ""
    prompt = DRAFT_PROMPT.format(
        outline=topics,
        goal=state["goal"],
        daily_minutes=state["daily_minutes"],
        deadline_line=deadline_line,
    )
    reply = await chat_model(IntelligenceTier.SMART).ainvoke([HumanMessage(prompt)])
    usage.record_message("plan_draft", reply)
    return {
        "stages": finalise_stages(
            parse_stages(reply.text),
            state["outline"]["topics"],
            state["daily_minutes"],
            date.fromisoformat(state["deadline"]) if state["deadline"] else None,
        )
    }


def _topics_for_prompt(outline: dict) -> str:
    ordered = [
        {"source_order": index + 1, **topic} for index, topic in enumerate(outline["topics"])
    ]
    return json.dumps(ordered, ensure_ascii=False, indent=1)


def order_stages_by_outline(stages: list[dict], outline_topics: list[dict]) -> list[dict]:
    """Make source order a deterministic invariant instead of a prompt hope.

    A stage covering several topics is placed when its latest topic is reached;
    this keeps cumulative reviews/capstones at the end. Unknown custom stages
    retain their relative order after material-backed stages.
    """
    topic_order = {
        _normalise_topic(str(topic["title"])): index for index, topic in enumerate(outline_topics)
    }

    def key(item: tuple[int, dict]) -> tuple[bool, int, int]:
        original_index, stage = item
        positions = [
            topic_order[normalised]
            for topic in stage.get("topics", [])
            if (normalised := _normalise_topic(str(topic))) in topic_order
        ]
        return (not positions, max(positions, default=0), original_index)

    ordered = [stage for _, stage in sorted(enumerate(stages), key=key)]
    return _renumber_daily_titles(ordered)


def finalise_stages(
    stages: list[dict],
    outline_topics: list[dict],
    daily_minutes: int,
    deadline: date | None = None,
    today: date | None = None,
) -> list[dict]:
    """Apply invariants that must not depend on model obedience.

    Topic titles are canonicalised to the outline, daily schedules fit the
    learner's daily budget, and a feasible deadline caps the aggregate estimate.
    """
    canonical = {
        _normalise_topic(str(topic["title"])): str(topic["title"]) for topic in outline_topics
    }
    cleaned: list[dict] = []
    for stage in stages:
        topics = []
        for topic in stage.get("topics", []):
            title = canonical.get(_normalise_topic(str(topic)))
            if title and title not in topics:
                topics.append(title)
        cleaned.append(
            {
                **stage,
                "topics": topics,
                "activities": list(dict.fromkeys(stage.get("activities", []))) or ["read"],
                "estimated_minutes": max(10, min(int(stage["estimated_minutes"]), 7 * 24 * 60)),
            }
        )

    ordered = order_stages_by_outline(cleaned, outline_topics)
    daily = all(_is_daily_title(stage["title"]) for stage in ordered)
    if daily:
        for stage in ordered:
            stage["estimated_minutes"] = min(stage["estimated_minutes"], daily_minutes)

    if deadline is not None and ordered:
        start = today or date.today()
        available_days = max(1, (deadline - start).days + 1)
        budget = available_days * daily_minutes
        total = sum(stage["estimated_minutes"] for stage in ordered)
        if total > budget and budget >= 10 * len(ordered):
            scale = budget / total
            for stage in ordered:
                stage["estimated_minutes"] = max(10, int(stage["estimated_minutes"] * scale))
    return ordered


def _is_daily_title(title: str) -> bool:
    return bool(re.search(r"第\s*\d+\s*天|\bday\s+\d+\b", title, re.IGNORECASE))


def _normalise_topic(value: str) -> str:
    return " ".join(value.casefold().split())


def _renumber_daily_titles(stages: list[dict]) -> list[dict]:
    """Keep day labels consistent if deterministic topic sorting moved stages."""
    chinese = re.compile(r"第\s*\d+\s*天")
    english = re.compile(r"\bday\s+\d+\b", re.IGNORECASE)
    if not stages or not all(chinese.search(stage["title"]) for stage in stages):
        if not stages or not all(english.search(stage["title"]) for stage in stages):
            return stages
        for day, stage in enumerate(stages, 1):
            stage["title"] = english.sub(f"Day {day}", stage["title"], count=1)
        return stages

    for day, stage in enumerate(stages, 1):
        stage["title"] = chinese.sub(f"第{day}天", stage["title"], count=1)
    return stages


def parse_stages(text: str) -> list[dict]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("The plan reply was not valid JSON")
    stages = json.loads(match.group(0)).get("stages", [])
    cleaned = []
    for stage in stages:
        if not stage.get("title") or not stage.get("description"):
            continue
        cleaned.append(
            {
                "title": str(stage["title"])[:200],
                "description": str(stage["description"]),
                "topics": [str(t) for t in stage.get("topics", [])],
                "activities": [
                    a for a in stage.get("activities", []) if a in ("read", "chat", "quiz")
                ],
                "estimated_minutes": _safe_minutes(stage.get("estimated_minutes")),
            }
        )
    if not cleaned:
        raise ValueError("The plan reply contained no usable stages")
    return cleaned


def _safe_minutes(value: object) -> int:
    try:
        return max(10, int(value or 60))
    except (TypeError, ValueError):
        return 60


def build_planner_graph():
    builder = StateGraph(PlanState)
    builder.add_node("load_context", load_context)
    builder.add_node("draft", draft)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "draft")
    builder.add_edge("draft", END)
    return builder.compile()


planner_graph = build_planner_graph()
