.PHONY: setup dev dev-backend dev-frontend test lint typecheck build db-migrate db-reset clean

VENV := .venv/bin
PYTHON := $(VENV)/python
UVICORN := $(VENV)/uvicorn
PYTEST := $(VENV)/pytest
RUFF := $(VENV)/ruff
MYPY := $(VENV)/mypy

# One-shot bootstrap: uv, venv, deps, frontend, docker perms, server
setup:
	./scripts/setup.sh

setup-only:
	./scripts/setup.sh --no-start

# Development
dev: dev-backend dev-frontend

dev-backend:
	cd backend && $(CURDIR)/$(UVICORN) app.main:app --host 0.0.0.0 --port 8088 --reload --reload-dir app

dev-frontend:
	cd frontend && npm run dev

# Testing
test: test-backend test-frontend

test-backend:
	cd backend && $(CURDIR)/$(PYTEST) tests/ -v

test-frontend:
	cd frontend && npm test -- --run

# Linting
lint: lint-backend lint-frontend

lint-backend:
	$(RUFF) format --check backend/
	$(RUFF) check backend/

lint-frontend:
	cd frontend && npm run lint

# Type checking
typecheck: typecheck-backend typecheck-frontend

typecheck-backend:
	cd backend && $(CURDIR)/$(MYPY) app

typecheck-frontend:
	cd frontend && npm run typecheck

# Formatting
format:
	$(RUFF) format backend/
	$(RUFF) check --fix backend/
	cd frontend && npm run format

# Build
build:
	cd frontend && npm run build
	docker build -f docker/Dockerfile -t tunnel-manager:dev .

# Database
db-migrate:
	cd backend && $(CURDIR)/$(VENV)/alembic upgrade head

db-reset:
	rm -f backend/tm.db backend/tm.db-wal backend/tm.db-shm
	cd backend && $(CURDIR)/$(VENV)/alembic upgrade head

# Docker compose dev
up:
	docker compose -f docker/compose.dev.yml up -d

down:
	docker compose -f docker/compose.dev.yml down

logs:
	docker compose -f docker/compose.dev.yml logs -f manager

# Cleanup
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf frontend/dist frontend/node_modules/.vite
