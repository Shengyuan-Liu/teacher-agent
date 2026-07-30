"""Streaming runs for the planner and quiz graphs.

Every agent stream speaks the same event protocol, so the client renders one
collapsible call chain for all of them:

    stage        {agent, stage, label}      a step started
    stage_result {stage, result}            what the step returned
    usage        {...}                      tokens and cost for the turn
    done         payload                    the artefact that was produced
"""

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import date

from app.agents.planner import planner_graph
from app.agents.quiz import quiz_graph
from app.core.database import AsyncSessionLocal
from app.models import PlanStage, Question, StudyPlan
from app.services import usage
from app.services.providers import IntelligenceTier, model_trace
from app.services.trace import trace_value

PLAN_STEPS = {
    "load_context": "Loading topic outline",
    "draft": "Drafting stages",
}
QUIZ_STEPS = {
    "gather": "Collecting sections to quiz on",
    "generate": "Writing questions",
    "validate": "Checking answers against the material",
}
EXPLANATION_STEPS = {
    "load_context": "Collecting prerequisites, mastery and source passages",
    "generate": "Writing a grounded structured lesson",
}

PLAN_MODEL_TIERS = {"draft": IntelligenceTier.SMART}
QUIZ_MODEL_TIERS = {
    "generate": IntelligenceTier.SMART,
    "validate": IntelligenceTier.SMART,
}
EXPLANATION_MODEL_TIERS = {"generate": IntelligenceTier.SMART}


def _event(name: str, payload: dict | list) -> dict:
    return {"event": name, "data": json.dumps(payload, ensure_ascii=False)}


async def _run_graph(
    graph,
    state: dict,
    agent: str,
    steps: dict[str, str],
    model_tiers: dict[str, IntelligenceTier],
    results: dict,
    trace: list[dict],
) -> AsyncGenerator[dict, None]:
    order = list(steps)
    first = {"agent": agent, "stage": order[0], "label": steps[order[0]]}
    if tier := model_tiers.get(order[0]):
        first.update(model_trace(tier))
    yield _event("stage", first)

    async for update in graph.astream(state, stream_mode="updates"):
        for node, payload in update.items():
            if node not in steps:
                continue
            results.update(payload or {})
            detail = trace_value(payload or {})
            record = {"agent": agent, "stage": node, "label": steps[node], "result": detail}
            if tier := model_tiers.get(node):
                record.update(model_trace(tier))
            trace.append(record)
            result_event = {"stage": node, "result": detail}
            if tier := model_tiers.get(node):
                result_event.update(model_trace(tier))
            yield _event("stage_result", result_event)
            following = order[order.index(node) + 1 :]
            if following:
                next_stage = {
                    "agent": agent,
                    "stage": following[0],
                    "label": steps[following[0]],
                }
                if tier := model_tiers.get(following[0]):
                    next_stage.update(model_trace(tier))
                yield _event("stage", next_stage)


async def stream_plan(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    goal: str,
    daily_minutes: int,
    deadline: date | None,
) -> AsyncGenerator[dict, None]:
    turn = usage.start()
    state = {
        "workspace_id": str(workspace_id),
        "user_id": str(user_id),
        "goal": goal,
        "daily_minutes": daily_minutes,
        "deadline": deadline.isoformat() if deadline else None,
        "outline": {},
        "mastery": "",
        "stages": [],
    }
    results: dict = {}
    trace: list[dict] = []
    try:
        async for event in _run_graph(
            planner_graph, state, "planner", PLAN_STEPS, PLAN_MODEL_TIERS, results, trace
        ):
            yield event
    except Exception as exc:
        yield _event("error", {"message": str(exc)[:500]})
        return

    async with AsyncSessionLocal() as db:
        plan = StudyPlan(
            workspace_id=workspace_id,
            user_id=user_id,
            goal=goal,
            daily_minutes=daily_minutes,
            deadline=deadline,
        )
        db.add(plan)
        await db.flush()
        for position, stage in enumerate(results["stages"]):
            db.add(PlanStage(plan_id=plan.id, position=position, **stage))
        await db.commit()
        plan_id = str(plan.id)

    yield _event("usage", turn.as_payload())
    yield _event("done", {"plan_id": plan_id})


async def stream_quiz(
    workspace_id: uuid.UUID, user_id: uuid.UUID, count: int, topic: str | None
) -> AsyncGenerator[dict, None]:
    turn = usage.start()
    state = {
        "workspace_id": str(workspace_id),
        "user_id": str(user_id),
        "count": count,
        "topic": topic,
        "sections": [],
        "raw": [],
        "questions": [],
    }
    results: dict = {}
    trace: list[dict] = []
    try:
        async for event in _run_graph(
            quiz_graph, state, "quiz", QUIZ_STEPS, QUIZ_MODEL_TIERS, results, trace
        ):
            yield event
    except Exception as exc:
        yield _event("error", {"message": str(exc)[:500]})
        return

    async with AsyncSessionLocal() as db:
        ids = []
        for question in results["questions"]:
            row = Question(workspace_id=workspace_id, **question)
            db.add(row)
            await db.flush()
            ids.append(str(row.id))
        await db.commit()

    yield _event("usage", turn.as_payload())
    yield _event("done", {"question_ids": ids})
