"""Planner graph: load_context -> draft.

Takes the workspace outline plus the user's goal and time budget, and drafts
ordered stages. Persistence stays in the service layer.
"""

import json
import re
import uuid
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.agents.outline import ensure_outline
from app.services import usage
from app.services.providers import chat_model

DRAFT_PROMPT = """You are planning a course of self-study.

Topic outline of the material (with prerequisite ids):
{outline}

Learner's goal: {goal}
Time available: {daily_minutes} minutes per day{deadline_line}

Draft a staged study plan as JSON:
{{"stages": [{{"title": "...", "description": "...", "topics": ["topic title", ...],
"activities": ["read", "quiz", "chat"], "estimated_minutes": 120}}]}}

Rules:
- 3 to 8 stages; respect the prerequisite order of the outline.
- Each stage names the outline topics it covers and roughly how many minutes it
  needs in total; keep stages sized so daily progress is realistic.
- `description` says what to do and what "done" looks like, in 2-3 sentences.
- `activities` picks from: read, chat, quiz.
- Use the learner's language as inferred from the goal text; otherwise the
  material's language.
- Output only the JSON object."""


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
    topics = json.dumps(state["outline"]["topics"], ensure_ascii=False, indent=1)
    deadline_line = f"\nDeadline: {state['deadline']}" if state["deadline"] else ""
    prompt = DRAFT_PROMPT.format(
        outline=topics,
        goal=state["goal"],
        daily_minutes=state["daily_minutes"],
        deadline_line=deadline_line,
    )
    reply = await chat_model().ainvoke([HumanMessage(prompt)])
    usage.record_message("plan_draft", reply)
    return {"stages": parse_stages(reply.text)}


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
                "estimated_minutes": int(stage.get("estimated_minutes") or 60),
            }
        )
    if not cleaned:
        raise ValueError("The plan reply contained no usable stages")
    return cleaned


def build_planner_graph():
    builder = StateGraph(PlanState)
    builder.add_node("load_context", load_context)
    builder.add_node("draft", draft)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "draft")
    builder.add_edge("draft", END)
    return builder.compile()


planner_graph = build_planner_graph()
