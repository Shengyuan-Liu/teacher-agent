"""LangGraph agents.

One module per agent graph. Agents own the decision flow — retrieval, grading,
generation, tool calls — and lean on `app.rag` for the retrieval machinery.
Planner, quiz, lecture and search graphs belong here as they are built
(docs/06-agent-design.md).
"""
