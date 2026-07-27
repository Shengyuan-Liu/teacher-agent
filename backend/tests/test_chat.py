from httpx import AsyncClient


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
