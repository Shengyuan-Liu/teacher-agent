import json
import uuid

from httpx import AsyncClient

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import Message
from app.services import chat_stream, usage
from app.services.telemetry import _otlp_endpoint, _otlp_headers


def test_otlp_configuration_uses_standard_endpoint_and_header_formats():
    assert _otlp_endpoint("http://collector:4318") == "http://collector:4318/v1/traces"
    assert _otlp_endpoint("http://collector:4318/v1/traces") == ("http://collector:4318/v1/traces")
    assert _otlp_headers("api-key=secret%20value,tenant=teacher") == {
        "api-key": "secret value",
        "tenant": "teacher",
    }
    assert _otlp_headers('{"api-key":"secret"}') == {"api-key": "secret"}


async def _fake_agent_stream(
    session_id,
    question,
    force_web=False,
    user_id=None,
    intent_override=None,
    request_id=None,
):
    del force_web, user_id, intent_override, request_id
    turn = usage.start()
    yield {
        "event": "stage",
        "data": json.dumps(
            {
                "agent": "router",
                "stage": "router",
                "label": "Understanding request",
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "tier": "fast",
                "reasoning_effort": "none",
            }
        ),
    }
    usage.record("router", "gpt-5.6-luna", 3, 2)
    yield {
        "event": "stage_result",
        "data": json.dumps(
            {
                "stage": "router",
                "result": {"intent": "qa", "answer": question},
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "tier": "fast",
                "reasoning_effort": "none",
            }
        ),
    }
    async with AsyncSessionLocal() as db:
        message = Message(
            session_id=session_id,
            role="assistant",
            content=f"Observed answer: {question}",
        )
        db.add(message)
        await db.commit()
        message_id = str(message.id)
    yield {"event": "usage", "data": json.dumps(turn.as_payload())}
    yield {
        "event": "done",
        "data": json.dumps({"message_id": message_id, "grounded": True}),
    }


async def test_agent_trace_summary_and_isolated_replay(auth_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "observability_enabled", True)
    monkeypatch.setattr(settings, "otel_capture_content", True)
    monkeypatch.setattr(chat_stream, "_stream_answer_impl", _fake_agent_stream)

    workspace = (await auth_client.post("/workspaces", json={"name": "Traces"})).json()
    workspace_id = workspace["id"]
    session = (await auth_client.post(f"/workspaces/{workspace_id}/chat/sessions")).json()
    async with AsyncSessionLocal() as db:
        db.add(
            Message(
                session_id=uuid.UUID(session["id"]),
                role="user",
                content="Earlier question",
            )
        )
        await db.commit()
        db.add(
            Message(
                session_id=uuid.UUID(session["id"]),
                role="assistant",
                content="Earlier answer",
            )
        )
        await db.commit()
    response = await auth_client.post(
        f"/chat/sessions/{session['id']}/stream",
        json={"message": "Explain observability"},
    )
    assert response.status_code == 200

    listed = (await auth_client.get(f"/workspaces/{workspace_id}/observability/runs")).json()
    assert len(listed) == 1
    source = listed[0]
    assert source["status"] == "completed"
    assert source["intent"] == "qa"
    assert len(source["trace_id"]) == 32
    assert source["input"]["question"] == "Explain observability"
    assert source["input"]["history"] == [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
    ]
    assert source["model_config"]["fast"]["model"]
    assert source["model_config"]["security_policy_version"] == "1.0.0"

    detail = (
        await auth_client.get(f"/workspaces/{workspace_id}/observability/runs/{source['id']}")
    ).json()
    router_span = next(span for span in detail["spans"] if span["stage"] == "router")
    assert router_span["model"] == "gpt-5.6-luna"
    assert router_span["input_tokens"] == 3
    assert router_span["output_tokens"] == 2
    assert detail["output"]["content"] == "Observed answer: Explain observability"

    summary = (await auth_client.get(f"/workspaces/{workspace_id}/observability/summary")).json()
    assert summary["runs"] == 1
    assert summary["success_rate"] == 1.0
    assert summary["by_agent"][0]["name"] == "router"
    assert summary["by_model"][0]["name"] == "gpt-5.6-luna"

    replay = await auth_client.post(
        f"/workspaces/{workspace_id}/observability/runs/{source['id']}/replay/stream",
        json={},
    )
    assert replay.status_code == 200
    listed = (await auth_client.get(f"/workspaces/{workspace_id}/observability/runs")).json()
    replayed = next(run for run in listed if run["kind"] == "replay")
    assert replayed["replay_of_id"] == source["id"]
    assert replayed["session_id"] is None

    replay_detail = (
        await auth_client.get(f"/workspaces/{workspace_id}/observability/runs/{replayed['id']}")
    ).json()
    assert replay_detail["replay_comparison"]["source_run_id"] == source["id"]
    assert replay_detail["replay_comparison"]["output_changed"] is False


async def test_replay_is_disabled_when_content_capture_is_off(
    auth_client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "observability_enabled", True)
    monkeypatch.setattr(settings, "otel_capture_content", False)
    monkeypatch.setattr(chat_stream, "_stream_answer_impl", _fake_agent_stream)
    workspace = (await auth_client.post("/workspaces", json={"name": "Private traces"})).json()
    session = (await auth_client.post(f"/workspaces/{workspace['id']}/chat/sessions")).json()
    await auth_client.post(
        f"/chat/sessions/{session['id']}/stream",
        json={"message": "Do not retain this"},
    )
    run = (await auth_client.get(f"/workspaces/{workspace['id']}/observability/runs")).json()[0]
    assert run["input"]["question"] == "[REDACTED]"
    response = await auth_client.post(
        f"/workspaces/{workspace['id']}/observability/runs/{run['id']}/replay/stream",
        json={},
    )
    assert response.status_code == 409
