import json
import uuid

import pytest
from httpx import AsyncClient

from app.agents.lecture import (
    OUTLINE_SYSTEM,
    SECTION_SYSTEM,
    control_action,
    generate_lecture_outline,
    grade_lecture_answer,
    parse_grade,
    parse_input_decision,
    parse_outline,
)
from app.core.database import AsyncSessionLocal
from app.models import LectureSession
from app.rag.retriever import RetrievedChunk


class _LectureGraph:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def astream(self, state, stream_mode):
        assert stream_mode == "updates"
        self.calls.append(state)
        index = state["current_section_index"]
        context = [
            RetrievedChunk(
                chunk_id=f"chunk-{index}",
                source_id="source-1",
                source_title="Course notes",
                heading=f"Section {index + 1}",
                content=f"Grounded material for section {index + 1}.",
                score=0.95,
            )
        ]
        yield {
            "load_context": {
                "context": context,
                "plan_context": "Goal: understand the course",
                "mastery": "No evidence yet.",
            }
        }
        if state["mode"] == "start":
            yield {
                "outline": {
                    "title": "Foundations lecture",
                    "outline": [
                        {"title": "First idea", "objective": "Understand one", "query": "one"},
                        {"title": "Second idea", "objective": "Understand two", "query": "two"},
                    ],
                }
            }
        yield {
            "section": {
                "section_content": f"Grounded explanation for section {index + 1} [1].",
                "pending_check": {
                    "question": f"What is the key idea in section {index + 1}?",
                    "expected_answer": f"Reference answer {index + 1}",
                    "explanation": f"Reference explanation {index + 1}",
                    "source": 1,
                },
            }
        }


class _Chunk:
    text = "The inserted question is answered from the notes [1]."


class _QAGraph:
    async def astream(self, state, stream_mode):
        assert stream_mode == ["updates", "messages"]
        context = [
            RetrievedChunk(
                chunk_id="interruption",
                source_id="source-1",
                source_title="Course notes",
                heading="Clarification",
                content="The notes contain the clarification.",
                score=0.9,
            )
        ]
        yield "updates", {"retrieve": {"context": context}}
        yield "updates", {"grade": {"grounded": True}}
        yield "messages", (_Chunk(), {"langgraph_node": "generate"})
        yield "updates", {"generate": {"answer": _Chunk.text}}


class _Reply:
    usage_metadata = None
    response_metadata: dict = {}

    def __init__(self, text: str) -> None:
        self.text = text


class _ReplySequence:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls = 0

    async def ainvoke(self, _messages):
        reply = _Reply(self.replies[min(self.calls, len(self.replies) - 1)])
        self.calls += 1
        return reply


class _BrokenLectureGraph:
    async def astream(self, _state, stream_mode):
        assert stream_mode == "updates"
        if False:
            yield {}
        raise ValueError("Lecture Agent returned invalid JSON")


def test_lecture_parsers_and_controls_fail_safely():
    assert '{"title"' in OUTLINE_SYSTEM.format(language="Write in Chinese.")
    assert '{"content"' in SECTION_SYSTEM.format(language="Write in Chinese.")
    title, sections = parse_outline(
        '{"title":"Course","sections":[{"title":"A","objective":"Learn A","query":"A docs"}]}'
    )
    assert title == "Course"
    assert sections[0]["query"] == "A docs"
    assert parse_grade('{"score":1.4,"feedback":"Good"}') == (1.0, "Good")
    assert parse_input_decision("not json", "为什么这样？")[0] == "question"
    assert control_action("继续讲课") == "continue"
    assert control_action("暂停讲课") == "pause"
    assert control_action("结束这次讲课") == "stop"


@pytest.mark.asyncio
async def test_lecture_outline_repairs_invalid_json(monkeypatch):
    from app.agents import lecture

    model = _ReplySequence(
        [
            "```json\n{'title': 'broken'}\n```",
            '{"title":"Fixed","sections":[{"title":"A","objective":"Learn A","query":"A"}]}',
        ]
    )
    monkeypatch.setattr(lecture, "chat_model", lambda *_: model)
    result = await generate_lecture_outline(
        {
            "workspace_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "scope": "讲解指数分布",
            "plan_context": "No plan",
            "context": [
                RetrievedChunk(
                    chunk_id="chunk",
                    source_id="source",
                    source_title="Notes",
                    heading="Exponential distribution",
                    content="The exponential distribution models waiting time.",
                    score=1.0,
                )
            ],
        }
    )
    assert model.calls == 2
    assert result["title"] == "Fixed"
    assert result["outline_format_recovered"] is True


@pytest.mark.asyncio
async def test_lecture_grader_repairs_invalid_json(monkeypatch):
    from app.agents import lecture

    model = _ReplySequence(
        [
            'The result is {"score": 0.9, "feedback": "Correct",}',
        ]
    )
    monkeypatch.setattr(lecture, "chat_model", lambda *_: model)
    score, feedback, metadata = await grade_lecture_answer(
        "The sum is Gamma distributed.",
        {
            "question": "What distribution describes the sum?",
            "expected_answer": "A Gamma distribution.",
            "explanation": "Independent exponential waiting times add to Gamma.",
        },
        "Write in English.",
    )
    assert model.calls == 1
    assert (score, feedback) == (0.9, "Correct")
    assert metadata == {
        "format_recovered": True,
        "recovery_method": "removed_trailing_commas",
    }


@pytest.mark.asyncio
async def test_lecture_generation_error_is_persisted_and_retryable(
    auth_client: AsyncClient, monkeypatch
):
    from app.services import chat_stream

    monkeypatch.setattr(chat_stream, "lecture_graph", _BrokenLectureGraph())
    workspace = (await auth_client.post("/workspaces", json={"name": "Lecture recovery"})).json()
    session = (await auth_client.post(f"/workspaces/{workspace['id']}/chat/sessions")).json()
    response = await auth_client.post(
        f"/chat/sessions/{session['id']}/stream",
        json={"message": "给我上一节关于指数分布的 lecture", "intent": "lecture"},
    )
    assert response.status_code == 200
    assert '"status": "error"' in response.text
    assert '"action": "retry"' in response.text
    assert "event: done" in response.text
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    assert [message["role"] for message in history[-2:]] == ["user", "assistant"]
    assert history[-1]["artifacts"]["status"] == "error"
    assert history[-1]["trace"][-1]["stage"] == "lecture_recovery"
    await auth_client.delete(f"/workspaces/{workspace['id']}")


@pytest.mark.asyncio
async def test_chat_request_id_prevents_duplicate_turn_execution(auth_client: AsyncClient):
    workspace = (await auth_client.post("/workspaces", json={"name": "Idempotent chat"})).json()
    session = (await auth_client.post(f"/workspaces/{workspace['id']}/chat/sessions")).json()
    request_id = str(uuid.uuid4())
    path = f"/chat/sessions/{session['id']}/stream"
    body = {
        "message": "查看掌握度",
        "intent": "progress",
        "request_id": request_id,
    }
    first = await auth_client.post(path, json=body)
    duplicate = await auth_client.post(path, json=body)
    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert '"duplicate": true' in duplicate.text
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    assert [message["role"] for message in history] == ["user", "assistant"]
    await auth_client.delete(f"/workspaces/{workspace['id']}")


@pytest.mark.asyncio
async def test_lecture_grade_failure_preserves_checkpoint_and_finishes_stream(
    auth_client: AsyncClient, monkeypatch
):
    from app.services import chat_stream

    graph = _LectureGraph()

    async def classify(_message: str, _pending: str):
        return "answer", 0.99, "answer", {}

    async def broken_grade(_message: str, _check: dict, _language: str):
        raise ValueError("invalid JSON after repair")

    monkeypatch.setattr(chat_stream, "lecture_graph", graph)
    monkeypatch.setattr(chat_stream, "classify_lecture_input", classify)
    monkeypatch.setattr(chat_stream, "grade_lecture_answer", broken_grade)

    workspace = (await auth_client.post("/workspaces", json={"name": "Grade recovery"})).json()
    session = (await auth_client.post(f"/workspaces/{workspace['id']}/chat/sessions")).json()
    path = f"/chat/sessions/{session['id']}/stream"
    await auth_client.post(path, json={"message": "给我讲一节基础课程", "intent": "lecture"})
    failed = await auth_client.post(path, json={"message": "这是我的答案"})

    assert failed.status_code == 200
    assert "event: error" not in failed.text
    assert "event: done" in failed.text
    assert '"action": "retry_grade"' in failed.text
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    recovery = history[-1]
    assert recovery["artifacts"]["status"] == "waiting_check"
    assert recovery["artifacts"]["check_question"].startswith("What is")
    assert recovery["trace"][-1]["result"]["checkpoint_preserved"] is True

    async with AsyncSessionLocal() as db:
        lecture = await db.get(LectureSession, uuid.UUID(recovery["artifacts"]["lecture_id"]))
        assert lecture is not None
        assert lecture.status == "waiting_check"
        assert lecture.current_section_index == 0
        assert lecture.pending_check is not None
        assert "score" not in lecture.section_history[0]

    mastery = (await auth_client.get(f"/workspaces/{workspace['id']}/mastery")).json()
    assert mastery == []
    await auth_client.delete(f"/workspaces/{workspace['id']}")


@pytest.mark.asyncio
async def test_chat_lecture_teach_interrupt_pause_resume_and_complete(
    auth_client: AsyncClient, monkeypatch
):
    from app.services import chat_stream

    graph = _LectureGraph()

    async def classify(message: str, _pending: str):
        if "为什么" in message:
            return "question", 0.99, "asks for clarification"
        return "answer", 0.98, "attempts the understanding check"

    async def grade(_message: str, _check: dict, _language: str):
        return 0.8, "The central idea is correct; connect it to the example more explicitly."

    monkeypatch.setattr(chat_stream, "lecture_graph", graph)
    monkeypatch.setattr(chat_stream, "classify_lecture_input", classify)
    monkeypatch.setattr(chat_stream, "grade_lecture_answer", grade)
    monkeypatch.setattr(chat_stream, "qa_graph", _QAGraph())

    workspace = (await auth_client.post("/workspaces", json={"name": "Phase 4"})).json()
    session = (await auth_client.post(f"/workspaces/{workspace['id']}/chat/sessions")).json()
    stream_path = f"/chat/sessions/{session['id']}/stream"

    started = await auth_client.post(
        stream_path,
        json={"message": "给我讲一节基础课程", "intent": "lecture"},
    )
    assert started.status_code == 200
    assert '"type": "lecture"' in started.text
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    first_lesson = history[-1]
    assert first_lesson["artifacts"]["status"] == "waiting_check"
    assert first_lesson["artifacts"]["current_section"] == 1
    assert "Reference answer 1" not in json.dumps(first_lesson["trace"])
    assert first_lesson["trace"][-1]["model"] == "gpt-5.6-terra"
    listed = (await auth_client.get(f"/workspaces/{workspace['id']}/lectures")).json()
    assert listed[0]["chat_session_id"] == session["id"]
    detail = (
        await auth_client.get(f"/workspaces/{workspace['id']}/lectures/{listed[0]['id']}")
    ).json()
    assert detail["pending_check"]["question"].startswith("What is")
    assert "expected_answer" not in detail["pending_check"]

    answered = await auth_client.post(stream_path, json={"message": "It is the first idea."})
    assert answered.status_code == 200
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    assert history[-1]["artifacts"]["status"] == "active"
    assert {step["agent"] for step in history[-1]["trace"]} >= {"router", "lecture"}
    assert any(step.get("model") == "gpt-5.6-luna" for step in history[-1]["trace"])

    paused = await auth_client.post(stream_path, json={"message": "暂停讲课", "intent": "lecture"})
    assert paused.status_code == 200
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    assert history[-1]["artifacts"]["status"] == "paused"

    resumed = await auth_client.post(stream_path, json={"message": "继续讲课", "intent": "lecture"})
    assert resumed.status_code == 200
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    assert history[-1]["artifacts"]["status"] == "waiting_check"
    assert history[-1]["artifacts"]["current_section"] == 2
    assert graph.calls[-1]["mode"] == "section"

    interrupted = await auth_client.post(stream_path, json={"message": "为什么这个例子成立？"})
    assert interrupted.status_code == 200
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    interruption = history[-1]
    assert interruption["artifacts"]["status"] == "waiting_check"
    assert interruption["citations"][0]["source_title"] == "Course notes"
    assert {step["agent"] for step in interruption["trace"]} >= {"lecture", "qa"}

    completed = await auth_client.post(stream_path, json={"message": "It is the second idea."})
    assert completed.status_code == 200
    history = (await auth_client.get(f"/chat/sessions/{session['id']}/messages")).json()
    assert history[-1]["artifacts"]["status"] == "completed"

    async with AsyncSessionLocal() as db:
        lecture = await db.get(LectureSession, uuid.UUID(first_lesson["artifacts"]["lecture_id"]))
        assert lecture is not None
        assert lecture.status == "completed"
        assert len(lecture.section_history) == 2
        assert all(item["score"] == 0.8 for item in lecture.section_history)

    mastery = (await auth_client.get(f"/workspaces/{workspace['id']}/mastery")).json()
    assert {item["topic"] for item in mastery} == {"First idea", "Second idea"}
    await auth_client.delete(f"/workspaces/{workspace['id']}")
