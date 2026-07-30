import uuid

from httpx import AsyncClient
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents import qa
from app.rag.retriever import RetrievedChunk


class _FakeQAChat:
    def __init__(self, grade: str, answer: str):
        self.grade = grade
        self.answer = answer

    async def ainvoke(self, _messages):
        return AIMessage(content=self.grade)

    async def astream(self, _messages):
        yield AIMessageChunk(content=self.answer)


async def test_qa_acceptance_answers_from_material_with_a_citation(monkeypatch):
    async def retrieve(*_args, **_kwargs):
        return [
            RetrievedChunk(
                chunk_id="chunk-1",
                source_id="source-1",
                source_title="notes.md",
                heading="Definition",
                content="A monoid is a set with an associative operation and an identity element.",
                score=1.0,
            )
        ]

    monkeypatch.setattr(qa, "retrieve", retrieve)
    monkeypatch.setattr(
        qa,
        "chat_model",
        lambda *_: _FakeQAChat("YES", "A monoid has associativity and an identity [1]."),
    )

    answer, grounded = await qa.answer_question("What is a monoid?", uuid.uuid4())
    assert grounded is True
    assert answer.endswith("[1].")


async def test_qa_acceptance_declines_when_material_has_no_coverage(monkeypatch):
    async def retrieve(*_args, **_kwargs):
        return []

    monkeypatch.setattr(qa, "retrieve", retrieve)
    monkeypatch.setattr(
        qa,
        "chat_model",
        lambda *_: _FakeQAChat("NO", "The provided material does not cover that question."),
    )

    answer, grounded = await qa.answer_question("What is the weather today?", uuid.uuid4())
    assert grounded is False
    assert "does not cover" in answer


async def test_session_lifecycle_and_delete(auth_client: AsyncClient):
    ws = (await auth_client.post("/workspaces", json={"name": "Chats"})).json()

    session = (await auth_client.post(f"/workspaces/{ws['id']}/chat/sessions")).json()
    listed = (await auth_client.get(f"/workspaces/{ws['id']}/chat/sessions")).json()
    assert [s["id"] for s in listed] == [session["id"]]

    assert (await auth_client.delete(f"/chat/sessions/{session['id']}")).status_code == 204
    assert (await auth_client.get(f"/workspaces/{ws['id']}/chat/sessions")).json() == []
    assert (await auth_client.delete(f"/chat/sessions/{session['id']}")).status_code == 404

    await auth_client.delete(f"/workspaces/{ws['id']}")


async def test_session_is_private_to_owner(auth_client: AsyncClient, client: AsyncClient):
    import uuid

    ws = (await auth_client.post("/workspaces", json={"name": "Private chats"})).json()
    session = (await auth_client.post(f"/workspaces/{ws['id']}/chat/sessions")).json()

    other = f"mallory-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": other, "password": "password123"})
    tokens = (
        await client.post("/auth/login", json={"email": other, "password": "password123"})
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    assert (
        await client.delete(f"/chat/sessions/{session['id']}", headers=headers)
    ).status_code == 404
    assert (
        await client.get(f"/chat/sessions/{session['id']}/messages", headers=headers)
    ).status_code == 404

    await auth_client.delete(f"/workspaces/{ws['id']}")


class TestCitedNumbers:
    def test_reads_inline_markers(self):
        from app.services.chat_stream import _cited_numbers

        assert _cited_numbers("As shown [1], and also [3].") == {1, 3}

    def test_deduplicates(self):
        from app.services.chat_stream import _cited_numbers

        assert _cited_numbers("[2] and again [2]") == {2}

    def test_no_markers(self):
        from app.services.chat_stream import _cited_numbers

        assert _cited_numbers("No sources referenced here.") == set()
