import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app

BASE = "http://test/api/v1"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health(client: AsyncClient):
    res = await client.get(f"{BASE}/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


async def test_readiness_reaches_postgres_and_redis(client: AsyncClient):
    res = await client.get(f"{BASE}/health/ready")
    assert res.status_code == 200
    checks = res.json()["checks"]
    assert checks["postgres"]["pgvector"] is True
    assert checks["redis"]["reachable"] is True


async def test_web_search_is_disabled_by_default(client: AsyncClient, monkeypatch):
    # Independent of what this deployment's .env enables.
    monkeypatch.setattr(settings, "web_search_enabled", False)
    res = await client.get(f"{BASE}/capabilities")
    assert res.json()["web_search"] is False


async def test_capabilities_reports_llm_tiers(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_fast_model", None)
    monkeypatch.setattr(settings, "llm_smart_model", None)

    res = await client.get(f"{BASE}/capabilities")

    assert res.status_code == 200
    assert res.json()["llm_models"] == {
        "fast": "gpt-5.6-luna",
        "smart": "gpt-5.6-terra",
    }
