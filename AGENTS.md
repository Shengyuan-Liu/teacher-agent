# Repository instructions for coding agents

## Agent Engineering Log

Any material change to agent behavior must append an entry to
`docs/13-agent-engineering-log.md` in the same change.

This includes:

- Router intents, prompts, confidence rules, clarification and consent gates;
- task planning, orchestration, DAG/executor, tools and shared state;
- model/provider/tier selection, reasoning effort and pricing;
- RAG grounding, synthesis, structured output and retry/fallback behavior;
- Lecture, Quiz, Planner, Evaluation, Observability and Replay agent flows;
- agent-facing UI call chains, traces, benchmarks and quality gates.

Each entry must record the problem or observed failure, root cause, implemented
solution, verification evidence, trade-offs and remaining limitations. Never put
API keys, raw private user content or other secrets in the log. Link to a sanitized
fixture or repository path when a concrete failure is useful.
