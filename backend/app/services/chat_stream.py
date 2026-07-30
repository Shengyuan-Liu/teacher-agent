import json
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agents import language
from app.agents.explanation import explanation_graph
from app.agents.planner import revise_plan
from app.agents.qa import qa_graph
from app.agents.quiz import quiz_graph
from app.agents.router import Intent, clarification_options, route_intent
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import (
    ChatSession,
    Message,
    PlanStage,
    Question,
    ReviewItem,
    StudyPlan,
    TopicMastery,
)
from app.rag.retriever import RetrievedChunk
from app.services import usage
from app.services.agent_runs import (
    EXPLANATION_MODEL_TIERS,
    EXPLANATION_STEPS,
    QUIZ_STEPS,
)
from app.services.assessment import create_assessment_from_bank
from app.services.providers import IntelligenceTier, model_trace
from app.services.rate_limit import over_rate_limit
from app.services.trace import trace_value

log = structlog.get_logger()

HISTORY_TURNS = 6


EXCERPT_CHARS = 600
CITED = re.compile(r"\[(\d+)\]")


def _cited_numbers(answer: str) -> set[int]:
    return {int(n) for n in CITED.findall(answer)}


_QUIZ_TYPE_LABELS = {
    "single": "single choice",
    "multi": "multiple choice",
    "fill": "fill in the blank",
    "short": "short answer",
}


def _quiz_citations(questions: list[dict], sections: list[dict]) -> list[dict]:
    """Turn the excerpts questions are grounded in into citations the chat can
    render as hoverable [n], the same shape QA citations use. Numbered by the
    excerpt index the generator used, so any [n] the explanations mention lines
    up with the same source."""
    citations: list[dict] = []
    seen: set[int] = set()
    for q in questions:
        index = (q.get("source") or {}).get("index")
        if not index or index in seen:
            continue
        seen.add(index)
        section = sections[index - 1] if 1 <= index <= len(sections) else {}
        content = section.get("content") or ""
        citations.append(
            {
                "n": index,
                "chunk_id": (q["source"]).get("chunk_id") or "",
                "source_id": section.get("source_id") or "",
                "source_title": (q["source"]).get("title") or "",
                "heading": (q["source"]).get("heading"),
                "excerpt": content[:EXCERPT_CHARS],
                "truncated": len(content) > EXCERPT_CHARS,
                "images": [],
                "source_type": section.get("source_type"),
                "source_origin": section.get("source_origin"),
                "source_url": section.get("source_url"),
                "source_position": section.get("source_position"),
            }
        )
    citations.sort(key=lambda c: c["n"])
    return citations


def _render_quiz(questions: list[dict]) -> str:
    """Format generated questions as chat markdown (the quiz now lives in the
    conversation, not a separate tab)."""
    lines = ["## Practice questions", ""]
    for i, q in enumerate(questions, 1):
        kind = _QUIZ_TYPE_LABELS.get(q["type"], q["type"])
        lines.append(f"**{i}. {q['stem']}**  _({kind} · {q['difficulty']})_")
        for opt in q.get("options") or []:
            lines.append(f"- {opt}")
        answer = q["answer"]
        answer = ", ".join(answer) if isinstance(answer, list) else answer
        lines += ["", f"**Answer:** {answer}"]
        if q.get("explanation"):
            lines.append(q["explanation"])
        index = (q.get("source") or {}).get("index")
        if index:
            lines.append(f"*Source:* [{index}]")
        lines.append("")
    return "\n".join(lines)


async def _persist_questions(workspace_id: uuid.UUID, questions: list[dict]) -> list[str]:
    ids: list[str] = []
    async with AsyncSessionLocal() as db:
        for payload in questions:
            question = Question(
                workspace_id=workspace_id,
                type=payload["type"],
                difficulty=payload["difficulty"],
                stem=payload["stem"],
                options=payload.get("options"),
                answer=payload["answer"],
                explanation=payload["explanation"],
                source=payload.get("source"),
            )
            db.add(question)
            await db.flush()
            ids.append(str(question.id))
        await db.commit()
    return ids


PLAN_CHAT_STEPS = {
    "load": "Loading your plan and outline",
    "revise": "Revising the plan",
    "save": "Saving the plan",
}

QUIZ_MODEL_TIERS = {
    "generate": IntelligenceTier.SMART,
    "validate": IntelligenceTier.SMART,
}
PLAN_MODEL_TIERS = {"revise": IntelligenceTier.SMART}


def _format_current_plan(plan: StudyPlan | None) -> str:
    # Keep the done marker out of the titles themselves, or the model copies it
    # into the titles it returns and the completed-status match then fails.
    if plan is None:
        return ""
    lines = [f"Goal: {plan.goal}", "Stages:"]
    for i, stage in enumerate(plan.stages, 1):
        lines.append(f"{i}. {stage.title}: {stage.description}")
    done = [s.title for s in plan.stages if s.status == "done"]
    if done:
        lines.append("Stages the learner has already completed (context): " + "; ".join(done))
    return "\n".join(lines)


def _render_plan(stages: list[dict], done_titles: set[str]) -> str:
    """Render the plan as a GFM task list so it reads as a to-do list in chat;
    the Plan tab shows the same stages with checkboxes to tick off."""
    lines = ["## Study plan", ""]
    for i, stage in enumerate(stages, 1):
        box = "x" if stage["title"] in done_titles else " "
        hours = round(stage.get("estimated_minutes", 60) / 60, 1)
        lines.append(f"- [{box}] **{i}. {stage['title']}** (~{hours}h)")
        if stage.get("description"):
            lines.append(f"  {stage['description']}")
        topics = " · ".join(stage.get("topics") or [])
        acts = ", ".join(stage.get("activities") or [])
        if topics or acts:
            lines.append(f"  _{topics}{(' — ' + acts) if acts else ''}_")
    lines += ["", "_你可以继续在聊天里告诉我完成了哪一项，或要求调整计划。_"]
    return "\n".join(lines)


def _citations_payload(context: list[RetrievedChunk]) -> list[dict]:
    return [
        {
            "n": i,
            "chunk_id": c.chunk_id,
            "source_id": c.source_id,
            "source_title": c.source_title,
            "heading": c.heading,
            "excerpt": c.content[:EXCERPT_CHARS],
            "truncated": len(c.content) > EXCERPT_CHARS,
            "images": [i["id"] for i in c.images],
            "source_type": c.source_type,
            "source_origin": c.source_origin,
            "source_url": c.source_url,
            "source_position": c.source_position,
        }
        for i, c in enumerate(context, 1)
    ]


async def _run_quiz(
    session: ChatSession, question: str, stage, stage_result, trace: list[dict], turn
) -> AsyncGenerator[dict, None]:
    """Quiz intent: run the quiz graph over the user's request and render its
    questions as one chat message. The user's wording drives the topic, count
    and language; each question's source becomes a hoverable citation. Reuses
    the shared trace protocol so the call chain looks like any other run."""
    quiz_state = {
        "workspace_id": str(session.workspace_id),
        "user_id": str(session.user_id),
        "count": 5,
        "topic": question,
        "request": question,
        "language": language.instruction(question),
        "sections": [],
        "raw": [],
        "questions": [],
    }
    order = list(QUIZ_STEPS)
    yield stage(order[0], QUIZ_STEPS[order[0]], tier=QUIZ_MODEL_TIERS.get(order[0]))
    sections: list[dict] = []
    questions: list[dict] = []
    try:
        async for update in quiz_graph.astream(quiz_state, stream_mode="updates"):
            for node, payload in update.items():
                if node not in QUIZ_STEPS:
                    continue
                if node == "gather":
                    sections = payload["sections"]
                elif node == "validate":
                    questions = payload["questions"]
                yield stage_result(node, QUIZ_STEPS[node], payload, tier=QUIZ_MODEL_TIERS.get(node))
                following = order[order.index(node) + 1 :]
                if following:
                    yield stage(
                        following[0],
                        QUIZ_STEPS[following[0]],
                        tier=QUIZ_MODEL_TIERS.get(following[0]),
                    )
    except Exception as exc:
        log.error("chat.quiz_failed", session_id=str(session.id), error=str(exc))
        yield {"event": "error", "data": json.dumps({"message": str(exc)[:500]})}
        return

    citations = _quiz_citations(questions, sections)
    question_ids = await _persist_questions(session.workspace_id, questions)
    artifact = {
        "type": "practice_quiz",
        "questions": [
            {**payload, "id": question_id}
            for payload, question_id in zip(questions, question_ids, strict=True)
        ],
    }
    content = f"我准备了 {len(questions)} 道随堂练习。请在下面逐题作答并查看解析。"
    yield {"event": "token", "data": json.dumps({"delta": content})}
    yield {"event": "citations", "data": json.dumps(citations)}
    yield {"event": "artifact", "data": json.dumps(artifact, ensure_ascii=False)}
    spent = turn.as_payload()
    yield {"event": "usage", "data": json.dumps(spent)}
    async with AsyncSessionLocal() as db:
        message = Message(
            session_id=session.id,
            role="assistant",
            content=content,
            citations=citations or None,
            web_citations=[],
            used_web_search=False,
            usage=spent,
            trace=trace,
            artifacts=artifact,
        )
        db.add(message)
        await db.commit()
        yield {
            "event": "done",
            "data": json.dumps({"message_id": str(message.id), "grounded": True}),
        }


def _test_parameters(request: str) -> tuple[int, int]:
    count_match = re.search(r"(\d+)\s*(?:道|题|questions?)", request, re.I)
    time_match = re.search(r"(\d+)\s*(?:分钟|分|minutes?|mins?)", request, re.I)
    count = max(1, min(20, int(count_match.group(1)))) if count_match else 5
    minutes = max(1, min(120, int(time_match.group(1)))) if time_match else max(10, count * 2)
    return count, minutes


async def _run_test(
    session: ChatSession,
    user_id: uuid.UUID,
    question: str,
    stage,
    stage_result,
    trace: list[dict],
    turn,
) -> AsyncGenerator[dict, None]:
    """Prepare a timed assessment and return it as an inline chat card."""
    count, minutes = _test_parameters(question)
    quiz_state = {
        "workspace_id": str(session.workspace_id),
        "user_id": str(user_id),
        "count": count,
        "topic": question,
        "request": question,
        "language": language.instruction(question),
        "sections": [],
        "raw": [],
        "questions": [],
    }
    order = list(QUIZ_STEPS)
    yield stage(order[0], "Preparing assessment material", tier=QUIZ_MODEL_TIERS.get(order[0]))
    questions: list[dict] = []
    try:
        async for update in quiz_graph.astream(quiz_state, stream_mode="updates"):
            for node, payload in update.items():
                if node not in QUIZ_STEPS:
                    continue
                if node == "validate":
                    questions = payload["questions"]
                yield stage_result(
                    node, QUIZ_STEPS[node], payload, tier=QUIZ_MODEL_TIERS.get(node)
                )
                following = order[order.index(node) + 1 :]
                if following:
                    yield stage(
                        following[0],
                        QUIZ_STEPS[following[0]],
                        tier=QUIZ_MODEL_TIERS.get(following[0]),
                    )
        await _persist_questions(session.workspace_id, questions)
        yield stage("create_assessment", "Creating timed assessment")
        async with AsyncSessionLocal() as db:
            assessment = await create_assessment_from_bank(
                db,
                session.workspace_id,
                user_id,
                title=question[:300],
                count=min(count, len(questions)),
                time_limit_minutes=minutes,
            )
            await db.commit()
            assessment_id = str(assessment.id)
        yield stage_result(
            "create_assessment",
            "Creating timed assessment",
            {"assessment_id": assessment_id, "questions": len(questions), "minutes": minutes},
        )
    except Exception as exc:
        log.error("chat.test_failed", session_id=str(session.id), error=str(exc))
        yield {"event": "error", "data": json.dumps({"message": str(exc)[:500]})}
        return

    artifact = {"type": "assessment", "assessment_id": assessment_id}
    content = f"正式测试已准备好：{len(questions)} 道题，限时 {minutes} 分钟。完成后统一提交评分。"
    yield {"event": "token", "data": json.dumps({"delta": content})}
    yield {"event": "artifact", "data": json.dumps(artifact)}
    spent = turn.as_payload()
    yield {"event": "usage", "data": json.dumps(spent)}
    async with AsyncSessionLocal() as db:
        message = Message(
            session_id=session.id,
            role="assistant",
            content=content,
            citations=None,
            web_citations=[],
            used_web_search=False,
            usage=spent,
            trace=trace,
            artifacts=artifact,
        )
        db.add(message)
        await db.commit()
        yield {
            "event": "done",
            "data": json.dumps({"message_id": str(message.id), "grounded": True}),
        }


async def _run_review_or_progress(
    session: ChatSession,
    user_id: uuid.UUID,
    intent: Intent,
    stage,
    stage_result,
    trace: list[dict],
    turn,
) -> AsyncGenerator[dict, None]:
    label = "Loading due review questions" if intent == "review" else "Summarizing mastery"
    yield stage("load", label)
    async with AsyncSessionLocal() as db:
        if intent == "review":
            rows = list(
                await db.scalars(
                    select(ReviewItem)
                    .where(
                        ReviewItem.workspace_id == session.workspace_id,
                        ReviewItem.user_id == user_id,
                        ReviewItem.active.is_(True),
                        ReviewItem.due_at <= datetime.now(UTC),
                    )
                    .order_by(ReviewItem.due_at)
                )
            )
            artifact = {"type": "review", "review_ids": [str(row.id) for row in rows]}
            result = {"due_count": len(rows), "topics": [row.topic for row in rows]}
            content = (
                f"你现在有 {len(rows)} 道到期错题。可以直接在下面开始复习。"
                if rows
                else "目前没有到期错题。你可以让我出一组随堂练习。"
            )
        else:
            rows = list(
                await db.scalars(
                    select(TopicMastery)
                    .where(
                        TopicMastery.workspace_id == session.workspace_id,
                        TopicMastery.user_id == user_id,
                    )
                    .order_by(TopicMastery.score, TopicMastery.topic)
                )
            )
            items = [
                {
                    "topic": row.topic,
                    "score": row.score,
                    "attempts": row.attempts,
                    "correct_count": row.correct_count,
                }
                for row in rows
            ]
            artifact = {"type": "mastery", "items": items}
            result = {"topics": len(items), "weakest": items[:3]}
            content = (
                "这是你当前的知识点掌握情况，薄弱项已经排在前面。"
                if items
                else "还没有足够的作答记录。先做一次随堂练习或正式测试吧。"
            )
    yield stage_result("load", label, result)
    yield {"event": "token", "data": json.dumps({"delta": content})}
    yield {"event": "artifact", "data": json.dumps(artifact, ensure_ascii=False)}
    spent = turn.as_payload()
    yield {"event": "usage", "data": json.dumps(spent)}
    async with AsyncSessionLocal() as db:
        message = Message(
            session_id=session.id,
            role="assistant",
            content=content,
            citations=None,
            web_citations=[],
            used_web_search=False,
            usage=spent,
            trace=trace,
            artifacts=artifact,
        )
        db.add(message)
        await db.commit()
        yield {
            "event": "done",
            "data": json.dumps({"message_id": str(message.id), "grounded": True}),
        }


async def _run_plan(
    session: ChatSession,
    user_id: uuid.UUID,
    question: str,
    history: list[tuple[str, str]],
    stage,
    stage_result,
    trace: list[dict],
    turn,
) -> AsyncGenerator[dict, None]:
    """Plan intent: create or edit the workspace's study plan from the user's
    message, keeping the one evolving plan the Plan tab displays. Completed
    stages carry over, so the agent picks up where the learner left off."""
    yield stage("load", PLAN_CHAT_STEPS["load"])
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(
            select(StudyPlan)
            .options(selectinload(StudyPlan.stages))
            .where(StudyPlan.workspace_id == session.workspace_id, StudyPlan.user_id == user_id)
            .order_by(StudyPlan.created_at.desc())
        )
        current_text = _format_current_plan(plan)
        done_titles = {s.title for s in plan.stages if s.status == "done"} if plan else set()
        previous_plan = (
            {
                "goal": plan.goal,
                "daily_minutes": plan.daily_minutes,
                "deadline": plan.deadline,
            }
            if plan
            else None
        )
        current_plan = (
            {
                "id": str(plan.id),
                "goal": plan.goal,
                "daily_minutes": plan.daily_minutes,
                "deadline": plan.deadline.isoformat() if plan.deadline else None,
                "stages": [
                    {
                        "id": str(item.id),
                        "position": item.position,
                        "title": item.title,
                        "description": item.description,
                        "topics": item.topics,
                        "activities": item.activities,
                        "estimated_minutes": item.estimated_minutes,
                        "status": item.status,
                    }
                    for item in plan.stages
                ],
            }
            if plan
            else None
        )
    yield stage_result(
        "load",
        PLAN_CHAT_STEPS["load"],
        {"current_plan": current_plan},
    )

    yield stage("revise", PLAN_CHAT_STEPS["revise"], tier=PLAN_MODEL_TIERS["revise"])
    try:
        stages = await revise_plan(
            session.workspace_id,
            question,
            current_text,
            history,
            daily_minutes=previous_plan["daily_minutes"] if previous_plan else 60,
            deadline=previous_plan["deadline"] if previous_plan else None,
            user_id=user_id,
        )
    except Exception as exc:
        log.error("chat.plan_failed", session_id=str(session.id), error=str(exc))
        yield {"event": "error", "data": json.dumps({"message": str(exc)[:500]})}
        return
    yield stage_result(
        "revise",
        PLAN_CHAT_STEPS["revise"],
        {"stages": stages},
        tier=PLAN_MODEL_TIERS["revise"],
    )

    yield stage("save", PLAN_CHAT_STEPS["save"])
    async with AsyncSessionLocal() as db:
        plan = StudyPlan(
            workspace_id=session.workspace_id,
            user_id=user_id,
            goal=previous_plan["goal"] if previous_plan else question[:500],
            daily_minutes=previous_plan["daily_minutes"] if previous_plan else 60,
            deadline=previous_plan["deadline"] if previous_plan else None,
        )
        db.add(plan)
        await db.flush()
        for position, stage_data in enumerate(stages):
            db.add(
                PlanStage(
                    plan_id=plan.id,
                    position=position,
                    status="done" if stage_data["title"] in done_titles else "pending",
                    **stage_data,
                )
            )
        await db.commit()
    yield stage_result(
        "save",
        PLAN_CHAT_STEPS["save"],
        {"plan_id": str(plan.id), "saved_stages": len(stages)},
    )

    content = _render_plan(stages, done_titles)
    yield {"event": "token", "data": json.dumps({"delta": content})}
    spent = turn.as_payload()
    yield {"event": "usage", "data": json.dumps(spent)}
    async with AsyncSessionLocal() as db:
        message = Message(
            session_id=session.id,
            role="assistant",
            content=content,
            citations=None,
            web_citations=[],
            used_web_search=False,
            usage=spent,
            trace=trace,
        )
        db.add(message)
        await db.commit()
        yield {
            "event": "done",
            "data": json.dumps({"message_id": str(message.id), "grounded": True}),
        }


async def _run_explanation(
    session: ChatSession,
    user_id: uuid.UUID,
    question: str,
    stage,
    stage_result,
    trace: list[dict],
    turn,
) -> AsyncGenerator[dict, None]:
    state = {
        "workspace_id": str(session.workspace_id),
        "user_id": str(user_id),
        "topic": question,
        "outline": {},
        "mastery": [],
        "context": [],
        "graph": {},
        "explanation": "",
    }
    order = list(EXPLANATION_STEPS)
    yield stage(order[0], EXPLANATION_STEPS[order[0]])
    results: dict = {}
    try:
        async for update in explanation_graph.astream(state, stream_mode="updates"):
            for node, payload in update.items():
                if node not in EXPLANATION_STEPS:
                    continue
                results.update(payload or {})
                tier = EXPLANATION_MODEL_TIERS.get(node)
                yield stage_result(node, EXPLANATION_STEPS[node], payload, tier=tier)
                following = order[order.index(node) + 1 :]
                if following:
                    next_node = following[0]
                    yield stage(
                        next_node,
                        EXPLANATION_STEPS[next_node],
                        tier=EXPLANATION_MODEL_TIERS.get(next_node),
                    )
    except Exception as exc:
        log.error("chat.explanation_failed", session_id=str(session.id), error=str(exc))
        yield {"event": "error", "data": json.dumps({"message": str(exc)[:500]})}
        return

    content = results["explanation"]
    citations = _citations_payload(results["context"])
    used = _cited_numbers(content)
    citations = [citation for citation in citations if citation["n"] in used]
    yield {"event": "token", "data": json.dumps({"delta": content})}
    if citations:
        yield {"event": "citations", "data": json.dumps(citations)}
    artifact = {"type": "knowledge_graph", "graph": results["graph"]}
    yield {"event": "artifact", "data": json.dumps(artifact, ensure_ascii=False)}
    spent = turn.as_payload()
    yield {"event": "usage", "data": json.dumps(spent)}
    async with AsyncSessionLocal() as db:
        message = Message(
            session_id=session.id,
            role="assistant",
            content=content,
            citations=citations or None,
            web_citations=[],
            used_web_search=False,
            usage=spent,
            trace=trace,
            artifacts=artifact,
        )
        db.add(message)
        await db.commit()
        yield {
            "event": "done",
            "data": json.dumps(
                {"message_id": str(message.id), "grounded": True}
            ),
        }


async def stream_answer(
    session_id: uuid.UUID,
    question: str,
    force_web: bool = False,
    user_id: uuid.UUID | None = None,
    intent_override: Intent | None = None,
) -> AsyncGenerator[dict, None]:
    """Run the QA graph and yield SSE events: citations, token, done.

    The intent router picks web vs local Q&A. force_web short-circuits it for the
    explicit "search the web" suggestion click; otherwise the user's own wording
    decides. Either way web only runs when the deployment enabled it, and never
    as a silent fallback — an uncovered question declines and offers the button.
    """
    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        history = await db.scalars(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(HISTORY_TURNS)
        )
        history_pairs = [(m.role, m.content) for m in reversed(list(history))]

        db.add(Message(session_id=session_id, role="user", content=question))
        if session.title is None:
            session.title = question[:60]
        await db.commit()

    state = {
        "question": question,
        "history": history_pairs,
        "workspace_id": str(session.workspace_id),
        "context": [],
        "grounded": False,
        "answer": "",
        "intent": "qa",
        "web_query": "",
        "web_results": [],
        "web_citations": [],
    }

    citations: list[dict] = []
    web_citations: list[dict] = []
    used_web_search = False
    answer_parts: list[str] = []
    grounded = False
    trace: list[dict] = []
    turn = usage.start()

    # The flow that handled the turn, so the whole call chain reads "web" or
    # "qa" rather than a hardcoded label. Set once the router decides; the stage
    # helpers read it late so every step is tagged consistently.
    agent = "qa"

    def stage(
        name: str,
        label: str,
        agent_name: str | None = None,
        tier: IntelligenceTier | None = None,
    ) -> dict:
        payload = {"agent": agent_name or agent, "stage": name, "label": label}
        if tier is not None:
            payload.update(model_trace(tier))
        return {
            "event": "stage",
            "data": json.dumps(payload),
        }

    def stage_result(
        name: str,
        label: str,
        result,
        agent_name: str | None = None,
        tier: IntelligenceTier | None = None,
    ) -> dict:
        detail = trace_value(result)
        record = {
            "agent": agent_name or agent,
            "stage": name,
            "label": label,
            "result": detail,
        }
        if tier is not None:
            record.update(model_trace(tier))
        trace.append(record)
        payload = {"stage": name, "result": detail}
        if tier is not None:
            payload.update(model_trace(tier))
        return {
            "event": "stage_result",
            "data": json.dumps(payload, ensure_ascii=False),
        }

    # Show the router immediately while its model call is running. Web is still
    # selected only when the deployment allows it and this turn explicitly asks.
    explicitly_routed = force_web or intent_override is not None
    router_tier = None if explicitly_routed else IntelligenceTier.FAST
    yield stage("router", "Understanding request", agent_name="router", tier=router_tier)
    intent: Intent = "qa"
    decision = None
    if force_web and settings.web_search_enabled:
        intent = "web"
    elif intent_override is not None:
        intent = intent_override
    else:
        # History lets the router route follow-ups ("后面没了，补全" after a plan)
        # to the right agent instead of defaulting each turn to qa.
        decision = await route_intent(question, history_pairs)
        if decision.needs_clarification:
            options = clarification_options(
                decision, web_search_enabled=settings.web_search_enabled
            )
            artifact = {
                "type": "clarification",
                "question": "你希望我怎么继续？",
                "options": options,
            }
            yield stage_result(
                "router",
                "Understanding request",
                {
                    "intent": decision.intent,
                    "confidence": decision.confidence,
                    "alternatives": list(decision.alternatives),
                    "reason": decision.reason,
                    "action": "ask_user",
                },
                agent_name="router",
                tier=router_tier,
            )
            content = artifact["question"]
            yield {"event": "token", "data": json.dumps({"delta": content})}
            yield {"event": "artifact", "data": json.dumps(artifact, ensure_ascii=False)}
            spent = turn.as_payload()
            yield {"event": "usage", "data": json.dumps(spent)}
            async with AsyncSessionLocal() as db:
                message = Message(
                    session_id=session.id,
                    role="assistant",
                    content=content,
                    citations=None,
                    web_citations=[],
                    used_web_search=False,
                    usage=spent,
                    trace=trace,
                    artifacts=artifact,
                )
                db.add(message)
                await db.commit()
                yield {
                    "event": "done",
                    "data": json.dumps(
                        {"message_id": str(message.id), "grounded": True, "clarification": True}
                    ),
                }
            return
        intent = decision.intent or "qa"
    if intent == "web" and not settings.web_search_enabled:
        intent = "qa"
    agent = intent
    state["intent"] = intent
    _routed = {
        "web": "web search",
        "quiz": "a practice quiz",
        "test": "a timed assessment",
        "review": "due review questions",
        "progress": "a mastery summary",
        "plan": "a study plan",
        "explain": "a structured explanation",
    }.get(intent, "your material")
    yield stage_result(
        "router",
        "Understanding request",
        {
            "intent": intent,
            "routed_to": _routed,
            "forced_by_user_action": force_web,
            "selected_by_user": intent_override is not None,
            "confidence": decision.confidence if decision else 1.0,
            "alternatives": list(decision.alternatives) if decision else [],
            "reason": decision.reason if decision else "explicit user selection",
            "web_search_enabled": settings.web_search_enabled,
        },
        agent_name="router",
        tier=router_tier,
    )

    if (
        intent == "web"
        and user_id is not None
        and await over_rate_limit(
            user_id, "web_search", settings.web_search_rate_limit_per_hour, 3600
        )
    ):
        yield {
            "event": "error",
            "data": json.dumps({"message": "Web search rate limit reached. Try again later."}),
        }
        return

    if intent == "quiz":
        async for event in _run_quiz(session, question, stage, stage_result, trace, turn):
            yield event
        return

    if intent == "test" and user_id is not None:
        async for event in _run_test(
            session, user_id, question, stage, stage_result, trace, turn
        ):
            yield event
        return

    if intent in ("review", "progress") and user_id is not None:
        async for event in _run_review_or_progress(
            session, user_id, intent, stage, stage_result, trace, turn
        ):
            yield event
        return

    if intent == "plan" and user_id is not None:
        async for event in _run_plan(
            session, user_id, question, history_pairs, stage, stage_result, trace, turn
        ):
            yield event
        return

    if intent == "explain" and user_id is not None:
        async for event in _run_explanation(
            session, user_id, question, stage, stage_result, trace, turn
        ):
            yield event
        return

    # First real step, so the UI shows progress before the graph's first update.
    yield (
        stage("web_search", "Searching the web", tier=IntelligenceTier.FAST)
        if intent == "web"
        else stage("retrieve", "Searching material")
    )

    grade_tier: IntelligenceTier | None = None
    web_generate_tier: IntelligenceTier | None = None
    try:
        async for mode, payload in qa_graph.astream(state, stream_mode=["updates", "messages"]):
            if mode == "messages":
                chunk, metadata = payload
                if (
                    metadata.get("langgraph_node") in ("generate", "decline", "web_generate")
                    and chunk.text
                ):
                    answer_parts.append(chunk.text)
                    yield {"event": "token", "data": json.dumps({"delta": chunk.text})}
            elif mode == "updates":
                if "retrieve" in payload:
                    context = payload["retrieve"]["context"]
                    citations = _citations_payload(context)
                    yield stage_result("retrieve", "Searching material", payload["retrieve"])
                    grade_tier = IntelligenceTier.FAST if context else None
                    yield stage("grade", "Checking coverage", tier=grade_tier)
                if "grade" in payload:
                    grounded = payload["grade"]["grounded"]
                    yield stage_result(
                        "grade", "Checking coverage", payload["grade"], tier=grade_tier
                    )
                    if grounded:
                        yield stage(
                            "generate",
                            "Writing answer from material",
                            tier=IntelligenceTier.SMART,
                        )
                        yield {"event": "citations", "data": json.dumps(citations)}
                    else:
                        # Not covered: decline, and — only if the deployment allows
                        # web search — offer a button to go online. Never fire it
                        # ourselves; the click is the explicit action.
                        if settings.web_search_enabled:
                            yield {
                                "event": "web_search_suggested",
                                "data": json.dumps(
                                    {
                                        "reason": "not covered by the material",
                                        "suggested_query": question,
                                    }
                                ),
                            }
                        yield stage("decline", "Writing a decline", tier=IntelligenceTier.SMART)
                if "web_search" in payload:
                    used_web_search = True
                    web_citations = payload["web_search"]["web_citations"]
                    for wc in web_citations:
                        yield {"event": "web_citation", "data": json.dumps(wc)}
                    yield stage_result(
                        "web_search",
                        "Searching the web",
                        payload["web_search"],
                        tier=IntelligenceTier.FAST,
                    )
                    web_generate_tier = (
                        IntelligenceTier.SMART if payload["web_search"]["web_results"] else None
                    )
                    yield stage(
                        "web_generate",
                        "Answering from the web",
                        tier=web_generate_tier,
                    )
                if "generate" in payload:
                    yield stage_result(
                        "generate",
                        "Writing answer from material",
                        payload["generate"],
                        tier=IntelligenceTier.SMART,
                    )
                if "web_generate" in payload:
                    yield stage_result(
                        "web_generate",
                        "Answering from the web",
                        payload["web_generate"],
                        tier=web_generate_tier,
                    )
                if "decline" in payload:
                    yield stage_result(
                        "decline",
                        "Writing a decline",
                        payload["decline"],
                        tier=IntelligenceTier.SMART,
                    )
    except Exception as exc:
        log.error("chat.stream_failed", session_id=str(session_id), error=str(exc))
        yield {"event": "error", "data": json.dumps({"message": str(exc)[:500]})}
        return

    answer = "".join(answer_parts)
    # Retrieval returns top-k; only what the answer actually referenced belongs
    # in the citation list, or the reader is handed unrelated sources.
    used = _cited_numbers(answer)
    if used and grounded:
        citations = [c for c in citations if c["n"] in used]
        # The earlier event carried every candidate; replace it now that the
        # answer has shown which ones it leaned on.
        yield {"event": "citations", "data": json.dumps(citations)}
    if used and used_web_search:
        web_citations = [c for c in web_citations if c["n"] in used]

    spent = turn.as_payload()
    yield {"event": "usage", "data": json.dumps(spent)}

    async with AsyncSessionLocal() as db:
        message = Message(
            session_id=session_id,
            role="assistant",
            content=answer,
            citations=citations if grounded else None,
            web_citations=web_citations,
            used_web_search=used_web_search,
            usage=spent,
            trace=trace,
        )
        db.add(message)
        await db.commit()
        yield {
            "event": "done",
            "data": json.dumps({"message_id": str(message.id), "grounded": grounded}),
        }
