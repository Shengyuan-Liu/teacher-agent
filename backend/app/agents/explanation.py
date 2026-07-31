"""Grounded structured explanation plus a deterministic knowledge graph."""

import uuid
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.agents.outline import ensure_outline
from app.core.database import AsyncSessionLocal
from app.rag.retriever import RetrievalConfig, RetrievedChunk, retrieve
from app.services import usage
from app.services.agent_security import inspect_agent_output, sanitize_untrusted_content
from app.services.mastery import mastery_summary
from app.services.providers import IntelligenceTier, chat_model

EXPLANATION_PROMPT = """Create a structured lesson using only the supplied material.

Learner request: {topic}
Mastery evidence:
{mastery}

Relevant excerpts:
{excerpts}

Write GitHub-flavoured Markdown with:
1. a concise mental model;
2. prerequisite concepts;
3. a step-by-step explanation;
4. one worked example grounded in the excerpts;
5. common misconceptions;
6. three self-check questions without answers.

Use inline citations [1], [2], ... for every factual claim. Give extra attention
to low-mastery concepts. If the excerpts do not support part of the request,
say so instead of using outside knowledge. Mathematics must use $ delimiters."""


class ExplanationState(TypedDict):
    workspace_id: str
    user_id: str
    topic: str
    outline: dict
    mastery: list[dict]
    context: list[RetrievedChunk]
    graph: dict
    explanation: str


def build_knowledge_graph(outline: dict, mastery: list[dict]) -> dict:
    score_by_topic = {row["topic"].casefold(): row["score"] for row in mastery}
    nodes = [
        {
            "id": topic["id"],
            "title": topic["title"],
            "mastery": score_by_topic.get(topic["title"].casefold()),
        }
        for topic in outline.get("topics", [])[:30]
    ]
    known = {node["id"] for node in nodes}
    edges = [
        {"from": dependency, "to": topic["id"]}
        for topic in outline.get("topics", [])[:30]
        for dependency in topic.get("depends_on", [])
        if dependency in known
    ]
    return {"nodes": nodes, "edges": edges}


async def load_explanation_context(state: ExplanationState) -> dict:
    workspace_id = uuid.UUID(state["workspace_id"])
    user_id = uuid.UUID(state["user_id"])
    outline = await ensure_outline(workspace_id)
    async with AsyncSessionLocal() as db:
        rows = await mastery_summary(db, workspace_id, user_id, limit=30)
    mastery = [{"topic": row.topic, "score": row.score, "attempts": row.attempts} for row in rows]
    query = state["topic"].strip()
    if not query:
        query = " ".join(topic["title"] for topic in outline.get("topics", [])[:5])
    context = await retrieve(workspace_id, query, RetrievalConfig(top_k=6))
    if not context:
        raise ValueError("No relevant material found for this explanation")
    return {
        "outline": outline,
        "mastery": mastery,
        "context": context,
        "graph": build_knowledge_graph(outline, mastery),
    }


async def generate_explanation(state: ExplanationState) -> dict:
    excerpts = "\n\n".join(
        f"[{index}] ({item.source_title}"
        f"{' — ' + item.heading if item.heading else ''})\n"
        f"{sanitize_untrusted_content(item.content[:3000]).safe_text}"
        for index, item in enumerate(state["context"], 1)
    )
    mastery = (
        "\n".join(
            f"- {row['topic']}: {row['score']:.0f}% ({row['attempts']} attempts)"
            for row in state["mastery"]
        )
        or "No assessment evidence yet."
    )
    reply = await chat_model(IntelligenceTier.SMART).ainvoke(
        [
            HumanMessage(
                EXPLANATION_PROMPT.format(
                    topic=state["topic"] or "Explain the course foundations",
                    mastery=mastery,
                    excerpts=excerpts,
                )
            )
        ]
    )
    usage.record_message("explanation_generate", reply)
    guarded = inspect_agent_output(reply.text)
    return {
        "explanation": guarded.safe_text or "",
        "security": guarded.as_payload(),
    }


def build_explanation_graph():
    builder = StateGraph(ExplanationState)
    builder.add_node("load_context", load_explanation_context)
    builder.add_node("generate", generate_explanation)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "generate")
    builder.add_edge("generate", END)
    return builder.compile()


explanation_graph = build_explanation_graph()
