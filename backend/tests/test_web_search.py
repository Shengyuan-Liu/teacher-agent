import uuid

import pytest
from httpx import AsyncClient

from app.agents import qa
from app.agents.search import _dedup, _parse_list, _parse_objs
from app.core.config import settings
from app.rag.crawl import Page
from app.rag.search import SearchResult, WebSearchError, get_search_provider


class _Chunk:
    def __init__(self, text: str) -> None:
        self.text = text

    def __add__(self, other: "_Chunk") -> "_Chunk":
        return _Chunk(self.text + other.text)


class _Reply:
    text = "refined query"
    usage_metadata = None
    response_metadata: dict = {}


class _FakeChat:
    async def ainvoke(self, messages):
        return _Reply()

    async def astream(self, messages):
        yield _Chunk("answer [1]")


class _RouterChat:
    def __init__(self, verdict: str) -> None:
        self._verdict = verdict

    async def ainvoke(self, messages):
        reply = _Reply()
        reply.text = self._verdict
        return reply


def _state(**over) -> dict:
    base = {
        "config": None,
        "question": "how does it work",
        "history": [],
        "workspace_id": str(uuid.uuid4()),
        "context": [],
        "grounded": False,
        "answer": "",
        "intent": "qa",
        "web_query": "",
        "web_results": [],
        "web_citations": [],
    }
    base.update(over)
    return base


class TestSearchParsing:
    def test_parse_list(self):
        assert _parse_list('["a", "b"]') == ["a", "b"]

    def test_parse_list_tolerates_prose(self):
        assert _parse_list('here: ["x"] ok') == ["x"]

    def test_parse_objs(self):
        assert _parse_objs('[{"index": 0, "reason": "r"}]') == [{"index": 0, "reason": "r"}]

    def test_dedup_keeps_first_by_url(self):
        a = SearchResult("http://x", "t", "s", "x")
        b = SearchResult("http://x", "t2", "s2", "x")
        c = SearchResult("http://y", "t", "s", "y")
        assert [r.url for r in _dedup([[a, c], [b]])] == ["http://x", "http://y"]


class TestRedLineRouting:
    """The web search node is reachable only through the intent router; grade
    never routes to it. Structural, not prompt-based (CLAUDE.md red line)."""

    def test_qa_intent_goes_to_retrieve(self):
        assert qa._route_intent({"intent": "qa"}) == "retrieve"

    def test_missing_intent_goes_to_retrieve(self):
        assert qa._route_intent({}) == "retrieve"

    def test_web_intent_goes_to_search(self):
        assert qa._route_intent({"intent": "web"}) == "web_search"

    def test_grade_never_routes_to_web(self):
        assert qa._route_after_grade({"grounded": False}) == "decline"
        assert qa._route_after_grade({"grounded": True}) == "generate"


async def test_graph_never_searches_without_web_intent(monkeypatch):
    async def fake_retrieve(*a, **k):
        return []  # empty context -> grade short-circuits to not-grounded

    def must_not_run():
        raise AssertionError("web search must never be constructed without a web intent")

    monkeypatch.setattr(qa, "retrieve", fake_retrieve)
    monkeypatch.setattr(qa, "chat_model", lambda *_: _FakeChat())
    monkeypatch.setattr(qa, "get_search_provider", must_not_run)

    state = await qa.qa_graph.ainvoke(_state(intent="qa"))
    assert state["grounded"] is False
    assert state["web_citations"] == []


async def test_graph_searches_on_web_intent(monkeypatch):
    called = {"search": False}

    class _Provider:
        async def search(self, query, top_k, site_filter=None):
            called["search"] = True
            return [SearchResult("http://ex.com/a", "A", "snippet", "ex.com")]

    async def fake_retrieve(*a, **k):
        return []

    async def fake_fetch(url):
        return Page(url=url, title="A", markdown="page content")

    monkeypatch.setattr(qa, "retrieve", fake_retrieve)
    monkeypatch.setattr(qa, "chat_model", lambda *_: _FakeChat())
    monkeypatch.setattr(qa, "get_search_provider", lambda: _Provider())
    monkeypatch.setattr(qa, "fetch_page", fake_fetch)

    state = await qa.qa_graph.ainvoke(_state(intent="web"))
    assert called["search"] is True
    assert state["web_citations"][0]["domain"] == "ex.com"


async def test_router_defaults_to_qa(monkeypatch):
    from app.agents import router

    monkeypatch.setattr(router, "chat_model", lambda *_: _RouterChat("qa"))
    assert await router.classify_intent("explain markov chains") == "qa"


async def test_router_detects_explicit_web(monkeypatch):
    from app.agents import router

    monkeypatch.setattr(router, "chat_model", lambda *_: _RouterChat("web"))
    assert await router.classify_intent("上网查一下谁是 Markov") == "web"


async def test_router_detects_quiz(monkeypatch):
    from app.agents import router

    monkeypatch.setattr(router, "chat_model", lambda *_: _RouterChat("quiz"))
    assert await router.classify_intent("帮我出几道题考考我") == "quiz"


async def test_router_detects_structured_explanation(monkeypatch):
    from app.agents import router

    monkeypatch.setattr(router, "chat_model", lambda *_: _RouterChat("explain"))
    assert await router.classify_intent("系统讲解一下马尔可夫链并画知识图谱") == "explain"


async def test_router_detects_plan(monkeypatch):
    from app.agents import router

    monkeypatch.setattr(router, "chat_model", lambda *_: _RouterChat("plan"))
    assert await router.classify_intent("帮我制定一个学习计划") == "plan"


async def test_router_returns_clarification_for_ambiguous_request(monkeypatch):
    from app.agents import router

    reply = (
        '{"intent":"quiz","confidence":0.43,"alternatives":["test","explain"],'
        '"reason":"考我 could mean practice or a formal test"}'
    )
    monkeypatch.setattr(router, "chat_model", lambda *_: _RouterChat(reply))
    decision = await router.route_intent("考考我然后详细讲讲")

    assert decision.needs_clarification is True
    assert decision.intent == "quiz"
    assert decision.alternatives == ("test", "explain")
    assert [item["intent"] for item in router.clarification_options(
        decision, web_search_enabled=False
    )] == ["quiz", "test", "explain"]


def test_provider_without_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "search_provider", "tavily")
    monkeypatch.setattr(settings, "tavily_api_key", None)
    with pytest.raises(WebSearchError):
        get_search_provider()


async def test_capabilities_reports_flag(auth_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "web_search_enabled", False)
    res = await auth_client.get("/capabilities")
    assert res.status_code == 200
    assert res.json()["web_search"] is False


async def test_web_search_disabled_returns_403(auth_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "web_search_enabled", False)
    ws = (await auth_client.post("/workspaces", json={"name": "WS"})).json()
    res = await auth_client.post(f"/workspaces/{ws['id']}/web-search", json={"query": "x"})
    assert res.status_code == 403
    assert res.json()["detail"] == "WEB_SEARCH_DISABLED"
    await auth_client.delete(f"/workspaces/{ws['id']}")


async def test_ingest_disabled_returns_403(auth_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "web_search_enabled", False)
    ws = (await auth_client.post("/workspaces", json={"name": "WS"})).json()
    res = await auth_client.post(
        f"/workspaces/{ws['id']}/web-search/ingest",
        json={"results": [{"url": "https://example.com"}]},
    )
    assert res.status_code == 403
    await auth_client.delete(f"/workspaces/{ws['id']}")
