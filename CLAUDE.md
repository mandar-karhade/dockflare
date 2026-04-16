# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Before answering any question, reason step by step. Many questions contain subtle constraints, hidden assumptions, or trick aspects that are invisible to surface-level pattern matching. Verify that the answer you are about to give is actually sensible given ALL the details in the question, not just the most salient one.


## Repository State

This repo currently contains **only `docs/`** — a complete design spec for a self-hosted Cloudflare Tunnel Manager. No backend, frontend, Docker, or test code has been scaffolded yet. When asked to implement, start from `docs/11-implementation-plan.md` (phased roadmap) and `docs/12-project-structure.md` (directory layout).

**Do not invent code that contradicts the docs.** The docs are opinionated and internally consistent; implement what's written, flag disagreements as follow-ups.

## Authoritative Documents (read in this order)

1. `docs/README.md` — what and why
2. `docs/14-claude-code-guide.md` — working principles for coding agents (phases, fakes-before-reals, audit-everything)
3. `docs/11-implementation-plan.md` — phased build order with acceptance criteria
4. `docs/12-project-structure.md` — directory layout, `pyproject.toml` shape, naming conventions, dependency direction rules
5. `docs/01-architecture.md` — component topology and data flows

Deep-dives pulled in per-phase: `02-data-model.md`, `03-cloudflare-integration.md`, `04-docker-integration.md`, `05-api-specification.md`, `06-rotation-engine.md`, `07-reconciliation.md`, `08-security.md`, `09-frontend-spec.md`, `10-deployment.md`, `13-testing-strategy.md`.

## Intended Stack

- **Backend:** Python 3.12+, FastAPI, Pydantic v2, SQLModel + SQLAlchemy, Alembic, APScheduler, `httpx`, `docker` SDK, `cryptography`, `structlog`, `tenacity`
- **DB:** SQLite (WAL mode, FK pragma on via `event.listen`), Litestream replication to R2/S3
- **Frontend:** React 18+ / TypeScript / TanStack Query / shadcn/ui / Tailwind / Vite (pnpm)
- **Deploy:** Docker Compose + `tecnativa/docker-socket-proxy` + Docker secrets
- **Tunnel runtime:** per-container `cloudflare/cloudflared` sidecars (token-based, remotely-managed config)

## Planned Commands (wired in `Makefile` during Phase 0)

Targets per `docs/14-claude-code-guide.md`: `dev`, `test`, `lint`, `typecheck`, `build`, `db-migrate`, `db-reset`. Underlying tools:

- Lint/format: `ruff format` + `ruff check` with `select = ["E","F","I","UP","B","SIM","ASYNC","S"]`, line length 100
- Typecheck: `mypy` strict (Python), `@typescript-eslint/strict-type-checked` (TS)
- Tests: `pytest` with `asyncio_mode = "auto"`; tests mirror source (`app/services/foo.py` → `tests/unit/services/test_foo.py`). Run one with `pytest tests/unit/services/test_foo.py::test_name`.
- Migrations: `alembic upgrade head` / `alembic revision --autogenerate`
- Frontend: `pnpm install`, `pnpm build`, `pnpm dev`
- E2E: Playwright (see `docs/13-testing-strategy.md`)

## Non-Negotiable Architectural Rules

Load-bearing — violating these breaks the threat model or reconciliation loop.

- **DB is source of truth for intended state.** CF + Docker are "reality". Drift = mismatch. Reconcile via `reconcile_to_db` (push DB→reality) or `accept_external` (pull reality→DB). Never decide off a stale reality snapshot without reconciling first.
- **Fakes before reals.** Every external client ships with `FakeX` in `app/clients/*/fake.py` implementing the same `typing.Protocol`. Service-layer tests run against fakes — no network / no Docker.
- **Never mount `/var/run/docker.sock` into the manager directly.** Always via `socket-proxy`.
- **Secrets live in Docker secrets at `/run/secrets/*`, not env vars.** Exception: `TUNNEL_TOKEN` must be env on each `cloudflared` sidecar (cloudflared reads only env/argv).
- **`docker-py` is sync — wrap every call in `asyncio.to_thread` from async handlers.**
- **Split DB writes from CF/Docker calls across transactions.** Insert with `status='provisioning'` → external ops outside txn → update to `active`/`error`.
- **Idempotency at service layer.** Same-key create either noops or errors "already exists" — never half-state.
- **Every state-changing service method uses `audit_context`** (`app/core/audit.py`) to write success/failure `audit_log` rows.
- **Every `PUT /cfd_tunnel/{id}/configurations` ends with catch-all `{"service":"http_status:404"}`.** Use the shared ingress builder.
- **Never log secrets.** Redaction util required; lint rule forbids `logger.*` on `*_encrypted` / `*token*` fields (except `token_last_four`).
- **Explicit error hierarchy:** `TMError` → `NotFoundError`, `ConflictError` (→ `DNSConflictError`), `RotationError`, `CFAPIError`. No bare `Exception`. FastAPI handlers map to HTTP.
- **Re-detect conflicts server-side** on every resolution flow — never act on a client flag alone.
- **Pin `cloudflared` to a minor (e.g. `2024.x.x`), not `:latest`**; allow per-tunnel override in settings.

## Dependency Direction (enforced by import-linter in CI)

```
api      →  services  →  clients + core + models
workers  →  services + clients
realtime →  services + core
clients  →  core
services →  clients + models + core        (no api, no workers)
models   →  core
core     →  nothing
```

## Phase Discipline

Phases in `docs/11-implementation-plan.md` are sequential. Inside a phase: DB migration → service logic → API → GUI; tests alongside code. Phase 4 already gives a working happy-path tunnel manager; later phases harden. Don't build everything before v0.1.
