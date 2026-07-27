import uuid

from httpx import AsyncClient


async def test_register_login_me(client: AsyncClient):
    email = f"alice-{uuid.uuid4().hex[:8]}@example.com"

    res = await client.post(
        "/auth/register", json={"email": email, "password": "password123", "display_name": "Alice"}
    )
    assert res.status_code == 201

    res = await client.post("/auth/register", json={"email": email, "password": "password123"})
    assert res.status_code == 409

    res = await client.post("/auth/login", json={"email": email, "password": "wrong-password"})
    assert res.status_code == 401

    res = await client.post("/auth/login", json={"email": email, "password": "password123"})
    assert res.status_code == 200
    tokens = res.json()

    res = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert res.status_code == 200
    assert res.json()["email"] == email


async def test_me_rejects_missing_and_bad_tokens(client: AsyncClient):
    assert (await client.get("/auth/me")).status_code == 401
    res = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-token"})
    assert res.status_code == 401


async def test_refresh_rotates_tokens(client: AsyncClient):
    email = f"bob-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    tokens = (
        await client.post("/auth/login", json={"email": email, "password": "password123"})
    ).json()

    res = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert res.status_code == 200
    assert res.json()["access_token"]

    res = await client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert res.status_code == 401
