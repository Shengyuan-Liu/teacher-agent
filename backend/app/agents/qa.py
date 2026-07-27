"""Grounded Q&A graph: retrieve -> grade -> generate | decline.

The grade node is the hard gate against hallucination (docs/06-agent-design.md):
when the retrieved excerpts don't cover the question, we decline instead of
letting the model improvise.
"""

import uuid
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.agents.vision import image_blocks
from app.rag.retriever import RetrievalConfig, RetrievedChunk, retrieve
from app.services.providers import chat_model

TOP_K = 6

GENERATE_SYSTEM = """\
You are a study assistant. Answer the user's question using ONLY the numbered excerpts \
below. Cite the excerpts you rely on inline as [1], [2] and so on. Answer in the same \
language as the question. If the excerpts cover only part of the question, answer that \
part and say explicitly what the material does not cover. Never bring in outside knowledge.

Format your answer as strict GitHub-flavored Markdown:
- Use headings, lists and tables where they help.
- Write ALL mathematics in LaTeX between dollar delimiters: $x$ for inline, \
$$...$$ on its own lines for display equations. Never emit bare LaTeX commands, \
\\(...\\), \\[...\\], or unicode approximations outside dollar delimiters.
- Use fenced code blocks with a language tag for code.

Figures referenced by the excerpts are attached as images; describe what they \
show when it helps, and never claim to see a figure that was not attached.

Excerpts:
{excerpts}"""

GRADE_PROMPT = """\
Question: {question}

Excerpts:
{excerpts}

Can the question be answered, at least partially, from these excerpts alone? \
Reply with exactly one word: YES or NO."""

DECLINE_PROMPT = """\
The user asked: {question}

The provided study material does not contain information relevant to this question. \
Tell the user so, briefly and politely, in the same language as their question. \
Do not attempt to answer the question itself."""


class QAState(TypedDict):
    config: RetrievalConfig | None
    question: str
    history: list[tuple[str, str]]
    workspace_id: str
    context: list[RetrievedChunk]
    grounded: bool
    answer: str


def _format_excerpts(context: list[RetrievedChunk]) -> str:
    blocks = []
    for i, c in enumerate(context, 1):
        where = f"{c.source_title} — {c.heading}" if c.heading else c.source_title
        blocks.append(f"[{i}] ({where})\n{c.content}")
    return "\n\n".join(blocks)


async def retrieve_node(state: QAState) -> dict:
    context = await retrieve(
        uuid.UUID(state["workspace_id"]), state["question"], state.get("config")
    )
    return {"context": context}


async def grade(state: QAState) -> dict:
    if not state["context"]:
        return {"grounded": False}
    prompt = GRADE_PROMPT.format(
        question=state["question"], excerpts=_format_excerpts(state["context"])
    )
    reply = await chat_model().ainvoke([HumanMessage(prompt)])
    return {"grounded": "YES" in reply.text.upper()}


async def generate(state: QAState) -> dict:
    messages = [SystemMessage(GENERATE_SYSTEM.format(excerpts=_format_excerpts(state["context"])))]
    for role, content in state["history"]:
        messages.append({"role": role, "content": content})
    figures = image_blocks(state["context"])
    question: list[dict] | str = state["question"]
    if figures:
        question = [*figures, {"type": "text", "text": state["question"]}]
    messages.append(HumanMessage(content=question))
    # astream, not ainvoke: graph stream_mode="messages" only relays real
    # provider tokens, and the client renders them as they arrive.
    parts = []
    async for chunk in chat_model().astream(messages):
        parts.append(chunk.text)
    return {"answer": "".join(parts)}


async def decline(state: QAState) -> dict:
    parts = []
    async for chunk in chat_model().astream(
        [HumanMessage(DECLINE_PROMPT.format(question=state["question"]))]
    ):
        parts.append(chunk.text)
    return {"answer": "".join(parts)}


def build_qa_graph():
    builder = StateGraph(QAState)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("grade", grade)
    builder.add_node("generate", generate)
    builder.add_node("decline", decline)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "grade")
    builder.add_conditional_edges("grade", lambda s: "generate" if s["grounded"] else "decline")
    builder.add_edge("generate", END)
    builder.add_edge("decline", END)
    return builder.compile()


qa_graph = build_qa_graph()


async def answer_question(
    question: str, workspace_id: uuid.UUID, config: RetrievalConfig | None = None
) -> tuple[str, bool]:
    """One-shot answer. Used by the evaluation harness and by tests."""
    state = await qa_graph.ainvoke(
        {
            "config": config,
            "question": question,
            "history": [],
            "workspace_id": str(workspace_id),
            "context": [],
            "grounded": False,
            "answer": "",
        }
    )
    return state["answer"], state["grounded"]
