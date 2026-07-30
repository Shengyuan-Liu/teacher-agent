# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TeacherAgent: users supply learning material (PDF/Word/PPT/Excel/Markdown files, a URL or doc site, or a GitHub repo) and the agent grounds everything it produces in that material — Q&A, study plans, quiz banks, systematic explanations, and interactive lectures.

## Status

**Phases 0-2 are done and verified against real material.** What works end to end today:

| Area | State |
|---|---|
| Auth | Register / login / refresh, JWT, per-user isolation. Access tokens last an hour; the client refreshes on 401 and retries once. |
| Ingestion | Uploads (PDF/Word/PPT/Excel/Markdown), website crawl, GitHub repo. Runs in an arq worker with live progress on `sources.progress`, resumes jobs a dead worker left behind. |
| Retrieval | Parent/child chunks, dense + BM25 → RRF → rerank. Evaluated; numbers in `logs/rag_optimization.md`. |
| Agents | `qa`, `outline`, `planner`, `quiz` (`backend/app/agents/`). All stream over SSE with the shared trace protocol below. |
| Frontend | Login, workspace list, workspace with Chats / Plan / Quiz / Sources tabs, chat with citations, figures, collapsible call chain, per-turn cost. |

**Not built yet** — the natural next steps, in roadmap order (`docs/08-roadmap.md` Phase 3):

1. **Exam mode**: timed attempts over the existing question bank, objective auto-grading, LLM grading for `short` answers. Models for attempts/answers do not exist yet; `Question` does.
2. **Wrong-answer book + spaced repetition** (`ReviewItem` in `docs/04-data-model.md`, SM-2 style).
3. **Mastery tracking** per topic, feeding back into planner and quiz targeting.
4. **Systematic explanation** and the **Lecture** graph (needs LangGraph `interrupt` + Postgres checkpointer, which is why `psycopg` is already a dependency).
5. **Web search** — read the red line below before touching this.

`docs/` is the design source of truth for unbuilt parts. Read the relevant doc before building a feature rather than inventing a design.

## Commands

```bash
make setup              # one-time: containers + deps + schema
make dev                # backend (:8000) + worker + frontend (:5300), foreground
make dev-bg             # same, detached; logs land in logs/run_logs/<timestamp>-*.log
make stop               # stop the three dev processes (ARGS=--all also stops containers)
make test               # backend pytest + frontend vitest (containers must be up)
make lint               # ruff + oxlint + tsc
make fmt                # ruff format + autofix
make migrate            # alembic upgrade head
make migration m="what changed"
make reset-db           # drop the data volume and rebuild
```

Single backend test: `cd backend && uv run pytest tests/test_health.py::test_health`.
Single frontend test: `cd frontend && pnpm exec vitest run src/lib/sse.test.ts`.

RAG evaluation:

```bash
cd backend
uv run python -m app.rag.evaluation.runner build --workspace "Optimisation" --size 30
uv run python -m app.rag.evaluation.runner run --no-judge          # retrieval metrics only
uv run python -m app.rag.evaluation.runner run                     # adds LLM-judged metrics
```

Python deps are grouped as extras — `uv sync --extra openai --extra anthropic --extra ingestion`.

## Architecture

Design docs, in reading order: [docs/README.md](docs/README.md) indexes them. `01-requirements.md` (FR/NFR IDs are referenced elsewhere), `02-features.md` (per-module behaviour), `03-architecture.md`, `04-data-model.md`, `05-api-design.md`, `06-agent-design.md`, `08-roadmap.md` (phase order), `09-rag.md` (retrieval pipeline and evaluation).

**Layering:** `api/v1` routes → `services` → `agents` (LangGraph) → DB. Routes must not build prompts; they call agent entrypoints that return async generators, which FastAPI turns into SSE.

**Two homes, one boundary.** `backend/app/rag/` holds retrieval machinery — conversion, crawling, repo flattening, chunking, dense/sparse retrieval, fusion, reranking, evaluation. `backend/app/agents/` holds one module per LangGraph agent plus their helpers (`vision.py`, `language.py`). Agents decide, `rag` retrieves, `services/` is transport and persistence glue.

**Where LangGraph is and isn't used.** Q&A, planning, quiz generation, and later lecture and web search are graphs — they need multi-step decisions or pause/resume. Ingestion is a plain arq task chain; do not promote it to a graph. `agents/outline.py` is a single structured call, not a graph, for the same reason.

**Every agent stream speaks one protocol**, so the client renders one collapsible call chain for all of them (`services/agent_runs.py`, mirrored in `services/chat_stream.py`):

```
stage         {agent, stage, label}   a step started
stage_result  {stage, result}         complete JSON-safe structured node output
token         {delta}                 QA only, streamed answer text
citations     [...]                   QA only, narrowed to what the answer cited
usage         {...}                   tokens and cost for the whole turn
done          payload                 the artefact produced
```

QA traces persist on `messages.trace` so a reloaded conversation can still expand its chain. Adding an agent means: a graph in `agents/`, a step-label map in `agent_runs.py`, and JSON-safe node outputs that the shared trace serializer can stream and persist.

**Two persistence layers, deliberately.** Business data uses SQLAlchemy async + asyncpg. The LangGraph Postgres checkpointer uses psycopg (hence `psycopg[binary,pool]`). Both hit the same database. `settings.sync_database_url` exists for Alembic and the checkpointer.

**Vector storage** is pgvector in the same Postgres, behind a retriever abstraction so Qdrant can replace it. Changing the embedding model invalidates existing vectors.

**Chunks are parent/child.** `chunk_parents` holds section-sized context and is never embedded; `chunks` holds passage-sized children carrying the vectors. Retrieval matches children, de-duplicates to parents, and hands parents to the model — a passage embeds cleanly but alone would cut a theorem off from its proof. `app/rag/chunking.py` treats display maths, code fences, tables and Theorem/Proof/Definition environments as atomic and never splits them. Headings are capped (`MAX_HEADING`), because OCR sometimes emits an annotation blob on a heading line and an unbounded path overflows the column and fails the whole document.

**Retrieval is dense + BM25, fused with RRF, then reranked.** The two score scales are not comparable, so they combine by rank position, not value (`fusion.py`). `RetrievalConfig` switches each stage off, which is how the evaluation harness attributes a change to a stage. Measured on this corpus: reranking earns recall@1 (0.77 → 0.93); RRF *without* reranking is worse than dense alone, because BM25 is the weaker retriever here and drags the top position. `retrieve()` owns its own DB session and closes it before reranking — holding a pooled connection across a network call exhausts the pool under load.

**Evaluation lives in `app/rag/evaluation/` and writes to `logs/`.** The golden set is generated from the corpus, so the chunk a question came from is ground truth. Re-measure before claiming a retrieval change helped: the first golden set was too easy and hid every difference, and `logs/rag_optimization.md` only mixes runs from one golden set for that reason.

**PDFs are converted to Markdown, not read as text.** Plain extraction turns `‖x‖²` into `‖x‖` and `2` on separate lines, useless for maths. `app/rag/pdf_convert.py` picks a converter from `PDF_CONVERTER`: `mistral` ($2/1000 pages, pinned to `mistral-ocr-4` because `latest` drifts and OCR 3 drops some formulas as images), `gemini` (cheapest per page), or `text` (free, mangles maths). Only `text` works without an extra key.

**Figures survive ingestion.** Mistral OCR returns page images with `include_image_base64`; they land under `storage/<workspace>/images/<source>/` and the ids each section references are stored on `chunk_parents.images`. `agents/vision.py` attaches them to the answer prompt (capped by `MAX_ANSWER_IMAGES`) and the client fetches them from an authenticated endpoint. Without this the diagrams are silently lost, since the prose never restates what they show.

**Answers must match the question's language.** "Answer in the same language as the question" buried in a prompt was ignored in practice — an English question came back in Japanese. `agents/language.py` resolves the language and names it explicitly, using script detection where it is definitive (kana settles Chinese vs Japanese) and declining to guess on short Latin text, because a wrong name is worse than none.

**Cost is reported per turn, never guessed.** One question fans out into embedding, grade, rerank and generate calls, so `services/usage.py` accumulates them in a context-local ledger. Prices live in config; `MODEL_PRICES` and `RERANK_PRICES` **merge into** the defaults so adding one model does not drop the rest. A model with no configured price still reports tokens and leaves cost unknown. Rerankers billed per search go through `record_flat`.

**Chat models are selected by intelligence tier, not by a global model string at call sites.** Use `IntelligenceTier.FAST` for routing, classification, query rewriting, and LLM reranking; use `IntelligenceTier.SMART` for user-facing answers, outlines, plans, quizzes, and evaluation judges. OpenAI defaults these roles to GPT-5.6 Luna (`none` reasoning) and Terra (`medium` reasoning). Other providers reuse `LLM_MODEL` unless `LLM_FAST_MODEL` / `LLM_SMART_MODEL` overrides are configured. New model calls must choose a tier explicitly.

## Two design red lines

These come from `00-overview.md` and shape implementations across the codebase:

1. **Material is primary.** Answers, questions, and explanations must be traceable to a source chunk. When retrieval does not cover a question, say so — do not fall back on general knowledge. The QA graph's `grade` node is the gate that enforces this, and `quiz.py` drops any question whose answer is not in its options or whose source is out of range.

2. **Web search never fires on its own.** It runs only on an explicit user action, and results enter a workspace only after the user confirms them. Enforce this structurally: gate the tool on an `allow_web_search` flag in graph state so it is not even bound unless the request carried the user's intent. Never rely on prompt wording. See `02-features.md` §2.9.

## Code conventions

- English everywhere in code, comments, and LLM prompts. Chinese belongs only in `docs/` and `README.md`.
- Comments are rare and explain *why*; do not restate what the code does. Keep the density near what is already there.
- No comments in `pyproject.toml`.
- Do not write defensively. Let errors propagate instead of wrapping everything in try/except and returning degraded results. Timeouts, limits, and rate limits are configuration, not defensiveness — those belong.
- Verify with real data before claiming something works. Several bugs in this repo were found only by running the pipeline over the user's actual PDFs and sites, and one "passing" verification was wrong because the test tool normalised away the very bytes that broke the browser.

## Migrations

`alembic revision --autogenerate` is a starting point, not the answer. Three things it gets wrong here, each of which has already broken a run:

- **Enum labels and types.** New labels need an explicit `ALTER TYPE ... ADD VALUE`; dropped enums need a hand-written `DROP TYPE` on downgrade, or `downgrade base` then `upgrade head` fails on "type already exists".
- **NOT NULL columns on populated tables** need a `server_default`, or the upgrade aborts on existing rows.
- **JSONB defaults.** Assigning Python `None` stores a JSON `null` scalar, not SQL NULL, which then breaks `jsonb_array_length` and `IS NOT NULL`. Store `[]`/`{}` instead.

Never paper over a mismatch with `alembic stamp` — an empty migration means a fresh database will not reproduce the schema.

## Environment notes

- **WSL runs in mirrored networking mode**, so ports held by Windows are unavailable inside WSL. 5173–5187 are taken, which is why the frontend is pinned to 5300 with `strictPort` (override with `FRONTEND_PORT`). Vite proxies `/api` to :8000 in dev.
- **This machine's proxy uses fake-IP DNS** (hosts resolve into 198.18.0.0/15), so the crawler's SSRF guard is off here via `CRAWL_BLOCK_PRIVATE_ADDRESSES=false` in `.env`. Leave it on in normal deployments.
- **`pkill -f "<pattern>"` kills the shell running it** when the pattern appears in its own command line. `scripts/stop.sh` resolves processes by listening port and excludes its own process tree; reuse it rather than ad-hoc pkill.
- `/health/ready` returns 500 when a dependency is down rather than a per-dependency report; check backend logs for which one. Redis has a 2s connect timeout so this fails fast.
- VSCode needs `backend/.venv/bin/python` selected manually, otherwise it reports every dependency as missing.
- `.env` is gitignored; `.env.example` documents every setting. The user's `.env` currently runs OpenAI for chat, OpenAI embeddings, Mistral OCR, and Cohere reranking.
