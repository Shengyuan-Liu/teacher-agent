.PHONY: help setup up down stop logs backend worker frontend dev dev-bg test lint fmt migrate migration reset-db ps

help:  ## List available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:  ## One-time: start containers, install deps, create tables
	@test -f .env || cp .env.example .env
	docker compose up -d
	cd backend && uv sync --extra openai --extra anthropic --extra ingestion
	cd frontend && pnpm install
	@sleep 3
	cd backend && uv run alembic upgrade head

up:  ## Start PostgreSQL and Redis
	docker compose up -d

stop:  ## Stop backend, worker and frontend (add ARGS=--all for containers too)
	@./scripts/stop.sh $(ARGS)

down:  ## Stop containers, keep data
	docker compose down

ps:  ## Show container status
	docker compose ps

logs:  ## Follow container logs
	docker compose logs -f

backend:  ## Run the API with reload on :8000
	cd backend && uv run uvicorn app.main:app --reload --port 8000

worker:  ## Run the ingestion worker
	cd backend && uv run arq app.workers.main.WorkerSettings

frontend:  ## Run the dev server on :5300
	cd frontend && pnpm dev

dev:  ## Start everything (ARGS=--nohup to detach, logs in logs/run_logs/)
	@./scripts/dev.sh $(ARGS)

dev-bg:  ## Start everything detached, logs in logs/run_logs/
	@./scripts/dev.sh --nohup

test:  ## Run backend and frontend tests, requires running containers
	cd backend && uv run pytest -v
	cd frontend && pnpm test

lint:  ## Check backend and frontend
	cd backend && uv run ruff check app tests && uv run ruff format --check app tests
	cd frontend && pnpm lint && pnpm exec tsc -b --noEmit

fmt:  ## Format backend code
	cd backend && uv run ruff format app tests && uv run ruff check --fix app tests

migrate:  ## Apply migrations
	cd backend && uv run alembic upgrade head

migration:  ## Generate a migration: make migration m="what changed"
	cd backend && uv run alembic revision --autogenerate -m "$(m)"

reset-db:  ## Drop the data volume and rebuild the schema
	docker compose down -v
	docker compose up -d
	@sleep 5
	cd backend && uv run alembic upgrade head
