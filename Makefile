# ESTARA Pro developer commands. See CLAUDE.md ("Commands").
# Host-side commands use the Python venv in .venv (created by `make install`) and read .env.

SHELL := /bin/bash
.DEFAULT_GOAL := help

-include .env
export

VENV        ?= .venv
PY          := $(abspath $(VENV))/bin/python
COMPOSE     ?= docker compose
BACKEND_RUN := $(COMPOSE) run --rm --no-deps backend
CHAT_LANG   ?= en
CHAT_LLM    ?= config

.PHONY: help install up down logs migrate seed-sample check check-backend check-dashboard \
        test-e2e chat eval config-check check-docker

help: ## List commands
	@grep -hE '^[a-z0-9-]+:.*?## ' Makefile | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

install: ## Create .venv with backend deps and install dashboard deps
	python3.12 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "backend[dev]"
	cd dashboard && npm ci --no-audit --no-fund

up: ## Start Postgres+pgvector, Redis, backend, worker and dashboard (Docker Compose)
	$(COMPOSE) up -d --build --wait

down: ## Stop the stack (keeps the database volume)
	$(COMPOSE) down

logs: ## Follow logs from all services
	$(COMPOSE) logs -f

migrate: ## Apply Alembic migrations to the Compose database
	$(BACKEND_RUN) alembic upgrade head

seed-sample: ## Load sample users and the sample inventory (reads SEED_* from .env)
	$(BACKEND_RUN) python -m app.seed

config-check: ## Validate client/client.config.yaml and the script files
	cd backend && $(PY) -m app.core.config

check: check-backend check-dashboard ## ruff, mypy, pytest, eslint, tsc

check-backend:
	@test -n "$(TEST_DATABASE_URL)" || { echo "Set TEST_DATABASE_URL (see .env.example)"; exit 1; }
	cd backend && $(PY) -m ruff check .
	cd backend && $(PY) -m ruff format --check .
	cd backend && $(PY) -m mypy
	cd backend && $(PY) -m pytest

check-docker: ## Backend checks inside Docker (no host Python needed), then dashboard checks
	$(COMPOSE) --profile tools run --rm --build checks
	$(MAKE) check-dashboard

check-dashboard:
	cd dashboard && npx eslint .
	cd dashboard && npx tsc --noEmit

test-e2e: ## Playwright dashboard tests (needs `make up` and `make seed-sample`)
	cd dashboard && E2E_ADMIN_EMAIL="$(SEED_ADMIN_EMAIL)" E2E_ADMIN_PASSWORD="$(SEED_ADMIN_PASSWORD)" \
		E2E_STAFF_EMAIL="$(SEED_STAFF_EMAIL)" E2E_STAFF_PASSWORD="$(SEED_STAFF_PASSWORD)" \
		npx playwright test

chat: ## Talk to the Sales Brain in the terminal: make chat CHAT_LANG=hi [CHAT_LLM=offline]
	cd backend && $(PY) -m app.brain.cli --lang $(CHAT_LANG) --llm $(CHAT_LLM) $(CHAT_ARGS)

eval: ## Conversation evals on the live LLM (wipes EVAL_DATABASE_URL, default TEST_DATABASE_URL)
	cd backend && $(PY) -m tests.evals.run $(EVAL_ARGS)
