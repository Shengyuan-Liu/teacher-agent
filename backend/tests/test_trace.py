import json
import uuid

import pytest

from app.core.config import settings
from app.rag.retriever import RetrievedChunk
from app.services.agent_runs import _run_graph
from app.services.providers import IntelligenceTier
from app.services.trace import trace_value


def test_trace_value_keeps_complete_retrieval_output():
    chunk = RetrievedChunk(
        chunk_id="chunk-1",
        source_id="source-1",
        source_title="Notes.pdf",
        heading="Convexity",
        content="The complete retrieved passage.",
        score=0.91,
        images=[{"id": "figure-1"}],
    )

    assert trace_value({"context": [chunk]}) == {
        "context": [
            {
                "chunk_id": "chunk-1",
                "source_id": "source-1",
                "source_title": "Notes.pdf",
                "heading": "Convexity",
                "content": "The complete retrieved passage.",
                "score": 0.91,
                "images": [{"id": "figure-1"}],
                "source_type": None,
                "source_origin": None,
                "source_position": None,
                "source_url": None,
                "page_start": None,
                "page_end": None,
            }
        ]
    }


def test_trace_value_converts_database_identifiers():
    identifier = uuid.uuid4()
    assert trace_value({"id": identifier}) == {"id": str(identifier)}


@pytest.mark.asyncio
async def test_agent_stream_emits_and_persists_full_node_result(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_smart_model", "gpt-5.6-terra")
    detail = {"stages": [{"title": "Foundations", "topics": ["Norms"]}]}

    class FakeGraph:
        async def astream(self, state, stream_mode):
            assert state == {"workspace_id": "workspace"}
            assert stream_mode == "updates"
            yield {"draft": detail}

    trace = []
    events = [
        event
        async for event in _run_graph(
            FakeGraph(),
            {"workspace_id": "workspace"},
            "planner",
            {"draft": "Drafting stages"},
            {"draft": IntelligenceTier.SMART},
            {},
            trace,
        )
    ]

    result = json.loads(events[1]["data"])
    assert result["stage"] == "draft"
    assert result["result"] == detail
    assert result["model"] == "gpt-5.6-terra"
    assert result["tier"] == "smart"
    assert trace[0]["result"] == detail
    assert trace[0]["model"] == "gpt-5.6-terra"
