# 11 — Implementation Plan

## Build Philosophy

- **Vertical slices over horizontal layers.** Each phase produces something runnable end-to-end before moving on.
- **Mock external systems first.** Use fakes for CF API and Docker until the shape of the code is right, then swap in real clients.
- **DB schema and migrations first in each phase.** Schema is hardest to change later.
- **Every phase has acceptance tests.** No "we'll test it later" phases.
- **The GUI can lag the API by one phase.** Build API + integration tests first, then wire up GUI screens.

## Phase 0 — Project Scaffold

**Goal:** Empty but buildable project. Contributors can `git clone && make dev` and get a running skeleton.

**Deliverables:**
- Backend directory structure (see [12-project-structure.md](12-project-structure.md))
- Frontend directory structure
- `pyproject.toml` with deps (FastAPI, SQLModel, Alembic, httpx, docker, cryptography, APScheduler, pytest, ruff, mypy)
- `package.json` with deps (React, TanStack Query, shadcn/ui, Vite, Vitest, Playwright)
- `Dockerfile` (multi-stage: build frontend, copy to backend image)
- `docker-compose.yml` for dev (with socket-proxy)
- `Makefile` with: `dev`, `test`, `lint`, `typecheck`, `build`, `db-migrate`
- Pre-commit config: ruff format, ruff check, mypy, eslint, prettier
- GitHub Actions workflow: test, lint, typecheck, build image
- README linking to docs

**Acceptance:**
- `make dev` starts both backend (with hot reload) and frontend (with HMR)
- `GET /api/v1/health` returns 200 with `{"status": "ok"}`
- `GET /` returns the React app's index.html
- `make test` runs (no tests yet, but pytest and vitest exit 0)
- CI pipeline green on a trivial change

## Phase 1 — Core Data Model + Vault

**Goal:** DB schema exists, migrations work, secrets can be encrypted and decrypted.

**Deliverables:**
- All SQLModel classes from [02-data-model.md](02-data-model.md)
- Alembic initial migration that creates entire schema
- `VaultService` with AES-GCM encrypt/decrypt
- Master key loading from file/secret
- Connection pool setup with WAL mode + foreign keys
- Generic CRUD base class for services
- Unit tests: vault round-trip, migration up/down, each model's field constraints

**Acceptance:**
- `alembic upgrade head` creates the full schema
- `alembic downgrade base` drops everything cleanly
- Encrypting then decrypting a token returns the original
- Tampered ciphertext fails decryption with a clear error
- All tables have appropriate indexes per the doc
- `pytest tests/unit/test_vault.py tests/unit/test_models.py` all pass

## Phase 2 — Cloudflare Client

**Goal:** Talk to CF API with retry, caching, and rate-limit awareness. No app logic yet.

**Deliverables:**
- `CloudflareClient` (async httpx-based) with methods for every endpoint in [03-cloudflare-integration.md](03-cloudflare-integration.md)
- Retry logic with exponential backoff
- Rate-limit handling (respect `Retry-After`)
- Response caching for zones (15 min TTL)
- `FakeCloudflareClient` for tests (in-memory state that mimics API behavior)
- Token verification + scope introspection
- VCR cassettes for key endpoints (recorded once from real CF, replayed in tests)

**Acceptance:**
- `CloudflareClient.verify_token()` returns parsed scope data
- `CloudflareClient.list_zones(account_id)` returns paginated zones
- `CloudflareClient.create_tunnel()` + `get_tunnel_token()` returns token
- Integration tests against `FakeCloudflareClient` cover happy paths and all error codes listed in doc
- `CloudflareClient` never logs raw token strings

## Phase 3 — Docker Client + Event Listener

**Goal:** Read Docker state and react to events.

**Deliverables:**
- `DockerClient` wrapper around docker-py, all sync calls wrapped in `asyncio.to_thread`
- Container discovery by compose labels
- Network inspection helpers
- Event listener async task with queue bridging
- `FakeDockerClient` for tests
- Label conventions module (single source of truth for label keys)
- Container cache service (DB-backed, event-driven)

**Acceptance:**
- `docker compose up` a sample project → manager's container_cache updates within 2s
- `docker kill` a container → event received, cache updated
- Can spawn a cloudflared container with proper labels, security_opt, read_only
- Can connect spawned container to an arbitrary network
- Integration test using real Docker via testcontainers-python: spawn, attach network, inspect, remove

## Phase 4 — Tunnel + Route Services (happy path)

**Goal:** Create a tunnel + route end-to-end against real CF and real Docker.

**Deliverables:**
- `TunnelService.create()` — creates CF tunnel, fetches token, spawns sidecar
- `TunnelService.delete()` — graceful shutdown, DNS cleanup, CF deletion
- `RouteService.create()` — full flow including zone resolution, DNS creation, ingress rebuild
- `RouteService.delete()` — reverse flow
- `DNSService` with zone resolution, conflict detection (happy-path only in this phase)
- Ingress builder (DB routes → CF config)
- Priority calculator
- API endpoints: POST/GET/DELETE `/tunnels`, POST/GET/DELETE `/routes`, `/zones`, `/containers`
- Audit log entries for every state change

**Acceptance:**
- Create a tunnel via API → CF tunnel exists, sidecar running, DB has row
- Create a route via API → DNS record exists with proper comment, ingress updated, public URL returns 200
- Delete route → DNS removed, ingress updated, traffic stops
- Delete tunnel → sidecar stopped, CF tunnel deleted, DB cleaned
- E2E test with Playwright: bootstrap → create tunnel → create route → verify HTTP response

## Phase 5 — Conflict Detection + Resolution

**Goal:** Handle the non-trivial DNS scenarios.

**Deliverables:**
- Full conflict detection from [07-reconciliation.md](07-reconciliation.md) (all 5 types)
- `dns_backups` flow: snapshot before replace, restore endpoint
- 409 response with resolution options
- Two-phase route creation: POST returns conflict → client re-POSTs with resolution
- GUI conflict dialog
- Orphan DNS record scan endpoint

**Acceptance:**
- Creating a route for a hostname with an external A record returns 409 with options
- POSTing resolution=`replace_with_backup` replaces the record and creates a backup
- GET `/dns/backups` lists the backup; POST `/dns/backups/{id}/restore` restores
- GET `/reconcile/orphans` detects tunnel-manager-prefixed records with no DB row
- E2E: seed a conflicting DNS record → try to create route → resolve via GUI → verify outcome

## Phase 6 — Rotation Engine

**Goal:** Scheduled + manual tunnel token rotation works with rolling restart.

**Deliverables:**
- APScheduler integration
- `RotationService.rotate_soft()` with rolling restart overlap
- `RotationService.force_recreate()` destructive path
- CF credential rotation
- `rotation_events` audit trail
- Scheduler tick that finds due tunnels and rotates with jitter
- API: POST `/tunnels/{id}/rotate`, POST `/tunnels/{id}/force-recreate`, POST `/rotation/bulk`
- Webhook notification on failure
- GUI rotation history view + "rotate now" button

**Acceptance:**
- Manual soft rotation on a test tunnel completes with <3s downtime (measured)
- Scheduler picks up a tunnel with `next_rotation_due` in the past and rotates it
- Rolling restart leaves no orphan sidecars on success
- Rolling restart on failure leaves old sidecar running (no traffic loss)
- Force recreate replaces CF tunnel ID, updates all DNS records, zero orphan resources
- Rotation events record accurate downtime
- Bulk rotation stops on first failure by default, has `continue_on_error` option

## Phase 7 — Reconciliation + Drift Detection

**Goal:** Periodic scans surface divergence; user can resolve.

**Deliverables:**
- `ReconciliationService.run_drift_scan()` covering all finding types
- Scheduled hourly scan job
- Ingress equivalence check
- Resolution handlers for each drift type
- API: POST `/reconcile/scan`, GET `/reconcile/findings`, POST `/reconcile/findings/{id}/resolve`
- Adopt flow: bulk import on bootstrap + per-tunnel `/reconcile/orphans/adopt`
- GUI drift view with expand/diff
- Startup reconciliation (sidecar existence checks)

**Acceptance:**
- Manually change ingress in CF dashboard → next drift scan creates a finding
- Resolve as `reconcile_to_db` → CF ingress restored to DB state
- Resolve as `accept_external` → DB routes updated to match CF
- Manually `docker rm` a managed sidecar → startup reconcile recreates it
- Bootstrap scan of an account with 3 pre-existing tunnels → all importable with route adoption

## Phase 8 — Frontend Polish

**Goal:** GUI parity with API; all views implemented.

**Deliverables:**
- All pages from [09-frontend-spec.md](09-frontend-spec.md)
- Full bootstrap wizard
- WebSocket integration with TanStack Query cache invalidation
- Responsive layout (desktop-first, tablet works)
- Light/dark mode toggle, persisted
- Toast notifications for all mutations
- Empty/loading/error states for every view
- Keyboard shortcuts: `g t` → tunnels, `g r` → routes, `/` → search
- Accessible modals with focus trap
- E2E test suite covering: bootstrap, create/edit/delete tunnel, create/edit/delete route, conflict resolution, drift resolution, rotation

**Acceptance:**
- Fresh install → bootstrap → dashboard → create tunnel → create route → site reachable, all through GUI
- WebSocket reconnects automatically within 5s of drop
- Lighthouse score: Performance ≥90, Accessibility ≥95
- Playwright test suite green

## Phase 9 — Hardening + Backups

**Goal:** Production-ready.

**Deliverables:**
- Litestream integration with R2 config
- Backup restore documentation tested
- Security headers (HSTS, CSP, XFO, etc.)
- Master key rotation flow
- Prometheus metrics endpoint
- `/api/v1/info` for health monitoring
- Log redaction audit (no tokens in any log path)
- Rate-limiting on write endpoints
- Session auth for GUI (single admin user)
- API token support for programmatic access
- Hadolint + image scanning in CI
- Docker image published to ghcr.io

**Acceptance:**
- Redaction audit script finds zero leaked tokens in fixture logs
- Litestream backup/restore verified end-to-end
- Master key rotation re-encrypts all secrets successfully
- Trivy/grype scan: no critical or high CVEs
- Playwright auth test: unauth'd access redirects to login, logged-in access works
- API token with read-only scope can GET but not POST/DELETE

## Phase 10 — Release

**Goal:** v1.0.0 tagged, deployable.

**Deliverables:**
- Pinned dep versions
- Release notes
- Migration guide from "compose cloudflared" setup
- Example VPS bootstrap script
- Published docs site (GitHub Pages or MkDocs Material)
- Community-facing README with screenshots + demo GIF

**Acceptance:**
- Image pulls and runs on fresh VPS following docs alone
- Migration from a real existing setup completes without manual DB edits
- All docs links resolve

## Milestones by Wall-Clock

Rough estimates assuming one experienced engineer + Claude Code, with typical async/uninterrupted work:

| Phase | Duration | Cumulative |
|-------|----------|------------|
| 0 | 1-2 days | 2 days |
| 1 | 2-3 days | 5 days |
| 2 | 3-4 days | 9 days |
| 3 | 3-4 days | 13 days |
| 4 | 4-5 days | 18 days |
| 5 | 2-3 days | 21 days |
| 6 | 4-5 days | 26 days |
| 7 | 3-4 days | 30 days |
| 8 | 5-7 days | 37 days |
| 9 | 3-4 days | 41 days |
| 10 | 1-2 days | 43 days |

Total: ~6-8 weeks for a polished v1.0.0. First useful milestone (Phase 4) in ~3 weeks.

## Dependency Graph

```
Phase 0 (scaffold)
    │
    ▼
Phase 1 (data model + vault)
    │
    ├───────┬───────┐
    ▼       ▼       ▼
Phase 2  Phase 3  (frontend shell can parallel)
(CF)     (Docker)
    │       │
    └───┬───┘
        ▼
Phase 4 (tunnel + route happy path)
        │
        ├───────┬───────┐
        ▼       ▼       ▼
   Phase 5  Phase 6  Phase 7
   (conflict)(rotate)(reconcile)
        │       │       │
        └───┬───┴───────┘
            ▼
       Phase 8 (frontend polish)
            │
            ▼
       Phase 9 (hardening)
            │
            ▼
       Phase 10 (release)
```

Phases 5, 6, 7 can partially overlap. Phase 8's GUI work for earlier phases can run alongside phases 5-7.

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| CF API changes breaking ingress format | Low | High | Pin cloudflared to exact version in sidecars; test suite with recorded responses |
| docker-py incompatibility with new Docker versions | Medium | Medium | CI matrix tests against Docker 24, 25, 26 |
| SQLite contention under load | Low | Medium | WAL mode + busy_timeout; monitor; swap to Postgres later if needed |
| Cloudflare rate limits during mass rotation | Medium | Low | Jitter + stagger already designed; bulk ops chunk with pauses |
| User loses master key | High | High | Bootstrap flow requires acknowledgment of backup; docs emphasize; surface key backup state in dashboard |
| Rolling restart fails in edge network conditions | Medium | Medium | Old sidecar not stopped until new confirmed healthy; automatic rollback |
| Accidental bulk deletion | Medium | High | Require explicit confirmation; audit log retention; DNS backups before destructive ops |

## Non-Goals for v1

- Multi-user with roles (v2)
- Multi-account federation (v2)
- TCP/SSH/RDP tunnel origin types (v1.1, maybe)
- Warp routing (private network mode)
- Custom cloudflared builds
- Non-SQLite databases
- Kubernetes operator mode
- Import/export as yaml/terraform

These are valid future features but deliberately deferred to keep v1 scope finite.
