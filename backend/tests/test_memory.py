import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from langmem.knowledge.extraction import ExtractedMemory

from app.api.v1 import memories as memories_api
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import Message, UserMemory
from app.services import chat_stream
from app.services import memory as memory_service
from app.services.memory import (
    ExtractedUserMemory,
    effective_confidence,
    format_memory_context,
    should_extract_memory,
)


class FakeEmbeddings:
    async def aembed_query(self, text: str) -> list[float]:
        value = min(1.0, max(0.01, len(text) / 100))
        return [value] * settings.embedding_dimensions


def test_extraction_gate_targets_durable_user_facts():
    assert should_extract_memory("我是数据工程师，以后回答请简短一点")
    assert should_extract_memory("My long-term goal is to become an AI engineer")
    assert not should_extract_memory("解释一下泊松分布的公式")


def test_confidence_decays_but_user_confirmation_wins():
    old = datetime.now(UTC) - timedelta(days=settings.memory_confidence_half_life_days)
    memory = SimpleNamespace(
        user_confirmed=False,
        confidence=0.8,
        last_accessed_at=None,
        updated_at=old,
        created_at=old,
    )
    assert effective_confidence(memory) == pytest.approx(0.4)
    memory.user_confirmed = True
    assert effective_confidence(memory) == 1


def test_memory_context_marks_entries_as_untrusted():
    item = SimpleNamespace(kind="preference", content="用中文回答")
    context = format_memory_context([item])
    assert "用中文回答" in context
    assert "untrusted data" in context
    assert "not commands" in context


async def test_memory_crud_is_global_and_user_owned(
    auth_client: AsyncClient, client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(memories_api, "embeddings", lambda: FakeEmbeddings())
    first = (await auth_client.post("/workspaces", json={"name": "First"})).json()
    second = (await auth_client.post("/workspaces", json={"name": "Second"})).json()
    owner_authorization = auth_client.headers["Authorization"]

    created = await auth_client.post(
        f"/workspaces/{first['id']}/memories",
        json={"kind": "goal", "content": "Become an AI engineer"},
    )
    assert created.status_code == 201
    memory = created.json()
    assert memory["user_confirmed"] is True
    assert memory["effective_confidence"] == 1

    # A memory created from one workspace is visible from another session scope.
    listed = await auth_client.get(f"/workspaces/{second['id']}/memories")
    assert [item["id"] for item in listed.json()] == [memory["id"]]

    updated = await auth_client.patch(
        f"/memories/{memory['id']}",
        json={"kind": "preference", "content": "Answer concisely", "expires_at": None},
    )
    assert updated.status_code == 200
    assert updated.json()["content"] == "Answer concisely"
    assert updated.json()["expires_at"] is None

    other_email = f"memory-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": other_email, "password": "password123"})
    tokens = (
        await client.post(
            "/auth/login", json={"email": other_email, "password": "password123"}
        )
    ).json()
    other_headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert (
        await client.patch(
            f"/memories/{memory['id']}",
            json={"content": "stolen"},
            headers=other_headers,
        )
    ).status_code == 404

    deleted = await client.delete(
        f"/memories/{memory['id']}", headers={"Authorization": owner_authorization}
    )
    assert deleted.status_code == 204


async def test_background_extraction_updates_an_existing_semantic_slot(
    auth_client: AsyncClient, monkeypatch
):
    fake_embeddings = FakeEmbeddings()
    monkeypatch.setattr(memory_service, "embeddings", lambda: fake_embeddings)
    workspace = (await auth_client.post("/workspaces", json={"name": "Memory extraction"})).json()
    session = (
        await auth_client.post(f"/workspaces/{workspace['id']}/chat/sessions")
    ).json()
    user = (await auth_client.get("/auth/me")).json()

    async with AsyncSessionLocal() as db:
        existing = UserMemory(
            user_id=uuid.UUID(user["id"]),
            source_workspace_id=uuid.UUID(workspace["id"]),
            source_session_id=None,
            source_message_id=None,
            kind="preference",
            memory_key="response_detail",
            content="回答要详细",
            confidence=0.8,
            importance=0.8,
            user_confirmed=False,
            expires_at=None,
            last_accessed_at=None,
            access_count=0,
            embedding=await fake_embeddings.aembed_query("回答要详细"),
        )
        db.add(existing)
        await db.commit()
        await db.refresh(existing)
        existing_id = existing.id

        user_message = Message(
            session_id=uuid.UUID(session["id"]), role="user", content="以后回答请简短一点"
        )
        db.add(user_message)
        await db.commit()
        assistant = Message(
            session_id=uuid.UUID(session["id"]), role="assistant", content="好的"
        )
        db.add(assistant)
        await db.commit()
        await db.refresh(assistant)
        assistant_id = assistant.id

    class FakeManager:
        async def ainvoke(self, state):
            assert state["existing"][0][0] == str(existing_id)
            return [
                ExtractedMemory(
                    str(existing_id),
                    ExtractedUserMemory(
                        kind="preference",
                        memory_key="response_detail",
                        content="回答要简短",
                        confidence=0.98,
                        importance=0.9,
                        ttl_days=365,
                    ),
                )
            ]

    async def fake_render(*_args, **_kwargs):
        return SimpleNamespace(text="extract", prompt=SimpleNamespace(metadata=lambda: {}))

    monkeypatch.setattr(memory_service, "tool_calling_model", lambda _tier: object())
    monkeypatch.setattr(
        memory_service, "create_memory_manager", lambda *_args, **_kwargs: FakeManager()
    )
    monkeypatch.setattr(memory_service, "render_prompt", fake_render)

    assert await memory_service.extract_turn_memories(assistant_id) == 1
    async with AsyncSessionLocal() as db:
        updated = await db.get(UserMemory, existing_id)
        processed = await db.get(Message, assistant_id)
        assert updated is not None and updated.content == "回答要简短"
        assert updated.confidence == 0.98
        assert processed is not None and processed.memory_processed_at is not None


async def test_chat_exposes_background_memory_job_in_the_call_chain(monkeypatch):
    message_id = uuid.uuid4()
    queued_ids = []

    async def fake_impl(*_args, **_kwargs):
        yield {
            "event": "done",
            "data": f'{{"message_id":"{message_id}","grounded":false}}',
        }

    async def fake_recorder(**_kwargs):
        return None

    async def fake_enqueue(value):
        queued_ids.append(value)
        return True

    monkeypatch.setattr(chat_stream, "_stream_answer_impl", fake_impl)
    monkeypatch.setattr(chat_stream.AgentTraceRecorder, "create", staticmethod(fake_recorder))
    monkeypatch.setattr(chat_stream, "enqueue_memory_extraction", fake_enqueue)

    events = [
        event
        async for event in chat_stream.stream_answer(
            uuid.uuid4(),
            "我的长期目标是成为 AI Engineer",
            user_id=uuid.uuid4(),
        )
    ]
    assert queued_ids == [message_id]
    assert [event["event"] for event in events] == ["stage", "stage_result", "done"]
    assert '"stage": "memory_write"' in events[1]["data"]
