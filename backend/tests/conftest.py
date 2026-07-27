import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Uploads in tests must not land in the real storage directory."""
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as ac:
        yield ac


@pytest.fixture
async def auth_client(client: AsyncClient):
    """A client logged in as a fresh user."""
    email = f"test-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    res = await client.post("/auth/login", json={"email": email, "password": "password123"})
    client.headers["Authorization"] = f"Bearer {res.json()['access_token']}"
    yield client
