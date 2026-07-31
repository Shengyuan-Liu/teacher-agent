import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.agents.explanation import build_knowledge_graph
from app.agents.router import AgentTask, IntentDecision
from app.core.database import AsyncSessionLocal
from app.models import AgentRun, PlanStage, Question, StudyPlan, Workspace
from app.services.assessment import grade_objective, parse_short_grade
from app.services.mastery import next_review_schedule


class _SynthesisReply:
    text = "Poisson 的身份来自网页 [1]；教材记录的相关定理来自本地资料 [2]。"
    usage_metadata = None
    response_metadata: dict = {}


class _SynthesisModel:
    def __init__(self) -> None:
        self.calls = 0
        self.prompt = ""

    async def ainvoke(self, messages):
        self.calls += 1
        self.prompt = "\n".join(str(message.content) for message in messages)
        return _SynthesisReply()


async def _web_context(_state):
    return {
        "web_query": "Who was Siméon Denis Poisson?",
        "web_results": [
            {
                "n": 1,
                "url": "https://example.com/poisson",
                "title": "Poisson biography",
                "markdown": "Poisson was a French mathematician.",
            }
        ],
        "web_citations": [
            {
                "n": 1,
                "url": "https://example.com/poisson",
                "title": "Poisson biography",
                "domain": "example.com",
                "fetched_at": "2026-07-30T00:00:00Z",
            }
        ],
    }


async def _rag_context(_state):
    from app.rag.retriever import RetrievedChunk

    return {
        "context": [
            RetrievedChunk(
                chunk_id="chunk-1",
                source_id="source-1",
                source_title="Probability textbook",
                heading="Poisson theorems",
                content="The textbook attributes the Poisson limit theorem to Poisson.",
                score=0.9,
            )
        ]
    }


class TestAssessmentGrading:
    def test_single_and_fill_are_normalised(self):
        single = {"type": "single", "answer": "Identity", "explanation": "Because."}
        fill = {"type": "fill", "answer": "Markov chain", "explanation": "Memoryless."}
        assert grade_objective(single, " identity ").correct
        assert grade_objective(fill, "MARKOV   CHAIN").correct

    def test_multi_requires_the_exact_set_not_the_order(self):
        snapshot = {
            "type": "multi",
            "answer": ["Associative", "Identity"],
            "explanation": "Both properties are required.",
        }
        assert grade_objective(snapshot, ["identity", "associative"]).correct
        assert not grade_objective(snapshot, ["identity"]).correct

    def test_short_grade_parser_clamps_and_fails_closed(self):
        assert parse_short_grade('{"score": 1.7, "feedback": "good"}') == (1.0, "good")
        score, feedback = parse_short_grade("not json")
        assert score == 0.0
        assert "invalid" in feedback


class TestReviewScheduling:
    def test_correct_reviews_expand_and_a_miss_resets(self):
        first = next_review_schedule(0, 0, 2.5, True)
        second = next_review_schedule(*first, True)
        third = next_review_schedule(*second, True)
        assert first[:2] == (1, 1)
        assert second[:2] == (2, 6)
        assert third[0] == 3 and third[1] > 6
        assert next_review_schedule(*third, False)[0:2] == (0, 1)


def test_knowledge_graph_keeps_dependencies_and_mastery():
    graph = build_knowledge_graph(
        {
            "topics": [
                {"id": "t1", "title": "Basics", "depends_on": []},
                {"id": "t2", "title": "Advanced", "depends_on": ["t1"]},
            ]
        },
        [{"topic": "Basics", "score": 72.0}],
    )
    assert graph["edges"] == [{"from": "t1", "to": "t2"}]
    assert graph["nodes"][0]["mastery"] == 72.0


@pytest.mark.asyncio
async def test_formal_test_updates_reviews_and_mastery(auth_client: AsyncClient):
    workspace = (await auth_client.post("/workspaces", json={"name": "Phase 3"})).json()
    workspace_id = uuid.UUID(workspace["id"])
    async with AsyncSessionLocal() as db:
        workspace_row = await db.get(Workspace, workspace_id)
        plan = StudyPlan(
            workspace_id=workspace_id,
            user_id=workspace_row.owner_id,
            goal="Learn probability",
            daily_minutes=45,
            stages=[],
        )
        db.add(plan)
        await db.flush()
        plan.stages.append(
            PlanStage(
                plan_id=plan.id,
                position=0,
                title="Foundations",
                description="Read the foundations.",
                topics=["Monoids", "Markov chains"],
                activities=["read"],
                estimated_minutes=60,
                status="pending",
            )
        )
        db.add_all(
            [
                Question(
                    workspace_id=workspace_id,
                    type="single",
                    difficulty="easy",
                    stem="Which property supplies a neutral element?",
                    options=["Identity", "Closure", "Inverse"],
                    answer="Identity",
                    explanation="The identity is the neutral element.",
                    source={"title": "Algebra", "heading": "Monoids"},
                ),
                Question(
                    workspace_id=workspace_id,
                    type="fill",
                    difficulty="medium",
                    stem="A process with the memoryless state property is a ____.",
                    options=None,
                    answer="Markov chain",
                    explanation="The Markov property makes the current state sufficient.",
                    source={"title": "Probability", "heading": "Markov chains"},
                ),
            ]
        )
        await db.commit()

    created = await auth_client.post(
        f"/workspaces/{workspace['id']}/assessments",
        json={"title": "Checkpoint", "count": 2, "time_limit_minutes": 10},
    )
    assert created.status_code == 201
    assessment = created.json()
    assert assessment["status"] == "in_progress"
    assert all("answer" not in question for question in assessment["questions"])

    answers = {}
    for question in assessment["questions"]:
        answers[question["id"]] = "Identity" if question["type"] == "single" else "Poisson process"
    submit_path = f"/workspaces/{workspace['id']}/assessments/{assessment['id']}/submit"
    submitted, duplicate = await asyncio.gather(
        auth_client.post(submit_path, json={"answers": answers}),
        auth_client.post(submit_path, json={"answers": answers}),
    )
    assert submitted.status_code == 200
    assert duplicate.status_code == 200
    result = submitted.json()
    assert result["status"] == "submitted"
    assert result["score"] == 1.0
    assert all("answer" in question for question in result["questions"])

    mastery = (await auth_client.get(f"/workspaces/{workspace['id']}/mastery")).json()
    assert {item["topic"] for item in mastery} == {"Monoids", "Markov chains"}
    assert all(item["attempts"] == 1 for item in mastery)

    plans = (await auth_client.get(f"/workspaces/{workspace['id']}/plans")).json()
    assert plans[0]["stages"][0]["title"].startswith("Targeted review: Markov chains")

    reviews = (await auth_client.get(f"/workspaces/{workspace['id']}/reviews")).json()
    assert len(reviews) == 1
    assert "answer" not in reviews[0]["question"]
    review = await auth_client.post(
        f"/workspaces/{workspace['id']}/reviews/{reviews[0]['id']}/answer",
        json={"response": "Markov chain"},
    )
    assert review.status_code == 200
    assert review.json()["correct"] is True
    assert review.json()["item"]["interval_days"] == 1
    assert (await auth_client.get(f"/workspaces/{workspace['id']}/reviews")).json() == []

    await auth_client.delete(f"/workspaces/{workspace['id']}")


@pytest.mark.asyncio
async def test_chat_router_asks_before_ambiguous_action(auth_client: AsyncClient, monkeypatch):
    from app.services import chat_stream

    async def ambiguous(*_args, **_kwargs):
        return IntentDecision("quiz", 0.42, ("test", "explain"), "multiple plausible actions")

    monkeypatch.setattr(chat_stream, "route_intent", ambiguous)
    workspace = (await auth_client.post("/workspaces", json={"name": "Clarify"})).json()
    session = (await auth_client.post(f"/workspaces/{workspace['id']}/chat/sessions")).json()
    response = await auth_client.post(
        f"/chat/sessions/{session['id']}/stream", json={"message": "考考我并详细讲讲"}
    )

    assert response.status_code == 200
    assert '"type": "clarification"' in response.text
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    assert history[-1]["artifacts"]["type"] == "clarification"
    assert [item["intent"] for item in history[-1]["artifacts"]["options"]] == [
        "quiz",
        "test",
        "explain",
    ]
    await auth_client.delete(f"/workspaces/{workspace['id']}")


@pytest.mark.asyncio
async def test_chat_choice_bypasses_router_model(auth_client: AsyncClient, monkeypatch):
    from app.services import chat_stream

    async def must_not_route(*_args, **_kwargs):
        raise AssertionError("explicit chat choice should bypass the router model")

    monkeypatch.setattr(chat_stream, "route_intent", must_not_route)
    workspace = (await auth_client.post("/workspaces", json={"name": "Choice"})).json()
    session = (await auth_client.post(f"/workspaces/{workspace['id']}/chat/sessions")).json()
    response = await auth_client.post(
        f"/chat/sessions/{session['id']}/stream",
        json={"message": "查看我的学习进度", "intent": "progress"},
    )

    assert response.status_code == 200
    assert '"type": "mastery"' in response.text
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    assert history[-1]["artifacts"] == {"type": "mastery", "items": []}
    await auth_client.delete(f"/workspaces/{workspace['id']}")


@pytest.mark.asyncio
async def test_one_chat_query_runs_web_and_rag_agents(auth_client: AsyncClient, monkeypatch):
    from app.services import chat_stream

    async def multi_plan(*_args, **_kwargs):
        return IntentDecision(
            "web",
            0.97,
            reason="request explicitly needs web and textbook evidence",
            tasks=(
                AgentTask("web", "Who was Siméon Denis Poisson?"),
                AgentTask("qa", "Which textbook theorems are attributed to Poisson?"),
            ),
        )

    async def within_limit(*_args, **_kwargs):
        return False

    answer_model = _SynthesisModel()
    monkeypatch.setattr(chat_stream, "route_intent", multi_plan)
    monkeypatch.setattr(chat_stream, "collect_web_context", _web_context)
    monkeypatch.setattr(chat_stream, "collect_rag_context", _rag_context)
    monkeypatch.setattr(chat_stream, "chat_model", lambda *_: answer_model)
    monkeypatch.setattr(chat_stream, "over_rate_limit", within_limit)
    monkeypatch.setattr(chat_stream.settings, "web_search_enabled", True)

    workspace = (await auth_client.post("/workspaces", json={"name": "Multi agent"})).json()
    session = (await auth_client.post(f"/workspaces/{workspace['id']}/chat/sessions")).json()
    response = await auth_client.post(
        f"/chat/sessions/{session['id']}/stream",
        json={"message": "上网搜一下 Poisson 是谁，然后看看他在这个教材里做出了哪些定理"},
    )

    assert response.status_code == 200
    assert '"agents": ["web", "qa"]' in response.text
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    answer = history[-1]
    assert answer["used_web_search"] is True
    assert [item["n"] for item in answer["web_citations"]] == [1]
    assert [item["n"] for item in answer["citations"]] == [2]
    assert {item["agent"] for item in answer["trace"]} >= {"router", "web", "qa", "answer"}
    dag_trace = next(item for item in answer["trace"] if item["stage"] == "task_dag")
    assert dag_trace["result"]["layers"] == [["web_1", "qa_1"], ["answer_1"]]
    answer_trace = next(item for item in answer["trace"] if item["stage"] == "answer_1")
    assert answer_trace["result"]["depends_on"] == ["web_1", "qa_1"]
    assert answer_trace["result"]["dag"]["nodes"][-1]["status"] == "completed"
    assert answer_trace["model"] == "gpt-5.6-terra"
    assert answer["trace"][-1]["stage"] == "resource_summary"
    assert answer_model.calls == 1
    assert "Poisson was a French mathematician" in answer_model.prompt
    assert "Poisson limit theorem" in answer_model.prompt
    async with AsyncSessionLocal() as db:
        run = await db.scalar(
            select(AgentRun)
            .where(AgentRun.session_id == uuid.UUID(session["id"]))
            .order_by(AgentRun.created_at.desc())
        )
        assert run is not None
        assert run.output_json["task_dag"]["nodes"][-1]["status"] == "completed"
    await auth_client.delete(f"/workspaces/{workspace['id']}")
