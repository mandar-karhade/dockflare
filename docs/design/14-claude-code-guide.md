# 14 — Claude Code Guide

This document is written for the coding agent (Claude Code, Cursor, etc.) that will implement this project. Read this first; it tells you which other docs to read, in what order, and how to work through the project.

## Read These In Order

1. **[README.md](README.md)** — 5 minutes. The what and why.
2. **[11-implementation-plan.md](11-implementation-plan.md)** — 10 minutes. The phased roadmap. This is your task breakdown.
3. **[12-project-structure.md](12-project-structure.md)** — 5 minutes. Where everything goes.
4. **[01-architecture.md](01-architecture.md)** — 15 minutes. Mental model for how components interact.

Then, for each phase you work on, pull in the relevant deep-dive docs as listed in the per-phase sections below.

## Working Principles

### 1. Follow the Phases

Phase N depends on Phase N-1. Don't skip ahead. If Phase 4 requires something from Phase 2 that doesn't exist, go back and build Phase 2 first rather than stubbing.

Inside a phase, there's flexibility on ordering, but always:
- Write the DB migration first if schema changes
- Write service logic before API handlers
- Write API handlers before GUI
- Write tests alongside code, not after

### 2. Fakes Before Reals

For every external client (CF, Docker), build the `FakeX` in `app/clients/*/fake.py` alongside the real one. All service-layer code should be testable against the fake. This is non-negotiable — without it, tests require network and Docker access, become slow and flaky, and the project stops being buildable in CI.

The fakes implement the same interface/protocol as the real clients. Use a `typing.Protocol` to define the interface if that helps enforcement.

### 3. Types Everywhere

- Every function argument and return type annotated.
- No `Any` except at actual boundaries (incoming JSON, etc.).
- Use Pydantic models for anything that crosses a boundary (HTTP, DB, external API).
- Frontend: strict TypeScript, no `any` without an eslint-disable comment explaining why.

### 4. Explicit Error Types

No bare `Exception` throws or catches in production code paths.

```python
# app/core/errors.py
class TMError(Exception): ...
class NotFoundError(TMError): ...
class ConflictError(TMError): ...
class DNSConflictError(ConflictError):
    def __init__(self, conflict_type: str, existing_record: dict, **kwargs):
        self.conflict_type = conflict_type
        self.existing_record = existing_record
        super().__init__(...)
class RotationError(TMError): ...
class CFAPIError(TMError):
    def __init__(self, cf_code: int, message: str):
        self.cf_code = cf_code
        super().__init__(message)
```

Handlers map these to HTTP responses via FastAPI exception handlers.

### 5. Audit Everything

Any state-changing service method writes to `audit_log`. The pattern:

```python
async def create_route(self, input: CreateRouteInput, actor: str) -> Route:
    async with audit_context(
        actor=actor,
        action="route.create",
        entity_type="route",
        db=self._db,
    ) as ctx:
        route = await self._do_create_route(input)
        ctx.set_entity_id(route.id)
        ctx.set_after(route.model_dump())
        return route
```

The context manager writes success/failure records automatically.

### 6. Never Log Secrets

Every log call for anything near a token goes through the redaction utility. Lint rule: no `logger.` call with an argument that comes from a model field with `*_encrypted` or `*token*` in its name, unless it's `token_last_four`.

### 7. Idempotency

Service methods must be safe to call twice. If a route is already created, `create_route` for the same hostname on the same tunnel should either succeed (noop) or fail with a specific "already exists" error — never leave half-state.

### 8. Single Source of Truth

- DB is authoritative for intended state
- CF/Docker are reality
- Drift = intended vs reality mismatch
- Reconciliation either pushes DB→reality (`reconcile_to_db`) or pulls reality→DB (`accept_external`)

Never let code make decisions from a stale snapshot of reality without reconciling back to DB first.

## Per-Phase Guide

### Phase 0: Scaffold

Docs: README, 11, 12.

- Create the directory structure from 12
- Backend: `pyproject.toml` with deps listed in 12
- Frontend: `package.json` with deps listed in 09 + 12
- `docker-compose.dev.yml`: manager + socket-proxy + a sample app for testing
- `Makefile` targets: `dev`, `test`, `lint`, `typecheck`, `build`, `db-migrate`, `db-reset`
- CI workflow from 13
- Empty `/api/v1/health` endpoint returning 200
- Empty React app rendering "tunnel-manager" text
- Precommit hooks

Commit early, commit often. The scaffold is boring but critical.

### Phase 1: Data Model + Vault

Docs: 02 (primary), 08 (for vault specifics).

- All SQLModel classes from 02
- Alembic env setup
- Initial migration `0001_initial_schema.py`
- `VaultService` — start here, it's small and fully testable
- DB engine with WAL pragma + FK enabled via `event.listen`
- Unit tests: vault round-trip, tamper detection, all model field constraints

Validate: run `alembic upgrade head` against a fresh SQLite, inspect schema with `sqlite3 tm.db .schema`, compare to doc.

### Phase 2: CF Client

Docs: 03 (primary).

- Start with the thin httpx wrapper (`get`, `post`, `put`, `patch`, `delete`)
- Add retry decorator using `tenacity`
- Add methods one endpoint at a time; each has a unit test against `FakeCloudflareClient`
- `FakeCloudflareClient`: in-memory dicts of accounts/zones/tunnels/records, methods that mutate state and raise appropriate errors
- Record VCR cassettes for each endpoint (one time, with a real test token)

Validate: write a script that uses the real client with a real test token, creates a tunnel, fetches token, deletes tunnel. Verify it works end-to-end.

### Phase 3: Docker Client + Events

Docs: 04 (primary).

- `DockerClient` wrapping `docker.from_env()` with all calls behind `asyncio.to_thread`
- Event listener as a standalone task
- Event router dispatching by type/action
- `FakeDockerClient` with seeded containers, methods to mutate and emit events
- Container cache service driven by events + periodic resync
- Labels module as single source of truth

Validate: `docker run` a container with specific labels, verify cache updates within 2s. Kill it, verify cache marks it gone.

### Phase 4: Tunnel + Route Services (Happy Path)

Docs: 05, 07 (skim for context).

- `TunnelService.create()` — walk through the flow in 01 data-flow section
- `RouteService.create()` — similar walk-through
- Use the `happy_path` only: assume DNS is clear, target container exists, no conflicts
- API handlers for tunnel, route, container, zone endpoints from 05
- Basic GUI for: list tunnels, create tunnel, list routes, create route
- E2E test that goes all the way through

Validate: bootstrap manager, create tunnel via GUI, create route, hit the public URL, get a response.

### Phase 5: Conflict Detection

Docs: 07 (primary).

- Conflict classifier — pure function, unit-testable
- DNS backup snapshots
- 409 response with resolution options
- Client-side conflict dialog
- Orphan scan endpoint

Validate: create a DNS record manually in CF, try to create a tunnel-manager route for the same hostname, verify you get 409 with correct type, resolve via GUI, verify backup created.

### Phase 6: Rotation

Docs: 06 (primary).

- APScheduler integration first, with just a "tick prints hello" job
- Then rolling restart mechanics — test with `FakeDockerClient` + `FakeCloudflareClient`
- Force recreate flow
- CF credential rotation
- Scheduler tick + due calculation
- GUI rotation history + button

Validate: manually set `next_rotation_due` in DB to the past, wait for next scheduler tick, verify rotation fires and sidecar gets restarted without traffic loss (use curl loop against the public URL during rotation).

### Phase 7: Reconciliation

Docs: 07 (primary, again).

- Drift detector for each finding type
- Scan scheduler
- Resolution handlers
- Adopt flow for bootstrap import
- Startup reconciliation
- GUI drift view

Validate: change an ingress rule directly in CF dashboard. Wait for drift scan. Verify finding appears. Resolve it via GUI. Verify CF matches DB again.

### Phase 8: Frontend Polish

Docs: 09 (primary).

- Go view-by-view: Dashboard → Tunnels → Routes → Containers → Drift → Settings → Bootstrap → Audit → Orphans
- Each view: loading, empty, error states
- WebSocket wiring for live updates
- Playwright tests for major flows

Validate: Lighthouse run, all scores ≥90 except SEO (ignore). Full E2E test suite green.

### Phase 9: Hardening

Docs: 08 (primary), 10.

- Security headers middleware
- Session auth + login page
- API token support
- Prometheus metrics
- Rate limiting
- Log redaction audit (run the script from `scripts/`)
- Litestream integration
- Trivy scan in CI

Validate: run `docker run --rm aquasec/trivy image tunnel-manager:latest`, must return zero CRITICAL/HIGH. Attempt to hit protected endpoint without auth, get 401. Run redaction-audit script on a corpus of fixture logs, zero leaks.

### Phase 10: Release

Docs: 10 (primary).

- Push image to ghcr.io
- Release notes
- Migration guide from existing setups
- Demo GIF/screenshots

Validate: on a clean VPS, follow the deployment doc end-to-end, reach a working bootstrap screen.

## Common Pitfalls

### Don't mount the Docker socket into the manager directly

Always go through `socket-proxy`. If you find yourself writing `/var/run/docker.sock:/var/run/docker.sock` in the manager compose service, stop.

### Don't put secrets in environment variables

Use Docker secrets mounted at `/run/secrets/*`. Env vars leak via `ps`, `docker inspect`, process lists.

Exception: `TUNNEL_TOKEN` must be in the env var of each cloudflared sidecar (cloudflared only reads from env or argv). This is by design and accepted — the sidecar is single-purpose and the token is isolated per-container.

### Don't trust the client on conflict resolution

Every conflict resolution flow must re-detect the conflict on the server side before acting. Client says "I chose replace_with_backup" → server still queries CF, still checks the record is what the client saw, then acts. Never act solely on a client flag.

### Don't block async with sync Docker calls

`docker-py` is synchronous. Every call in an async handler must go through `asyncio.to_thread(lambda: ...)`. Set a mental rule: if you write `docker_client.something()` in an `async def`, you're wrong.

### Don't catch-and-continue in rotation

If any step of rotation fails, stop. Don't catch-and-retry inside the service layer — let it propagate, record the failure in `rotation_events`, and surface via audit/UI/webhook. Retries happen at the scheduler layer on the next tick.

### Don't forget the catch-all ingress rule

Every `PUT /cfd_tunnel/{id}/configurations` must end with `{"service": "http_status:404"}`. The ingress builder adds this automatically — do not skip that builder and hand-craft config objects elsewhere.

### Don't let stale DB rows persist after external deletion

If CF dashboard deletes a tunnel out from under us, drift detection catches it but the DB row sticks around as `status=error, error_message=tunnel_missing_cf_side`. Don't auto-delete DB rows on drift; let the user decide.

### Don't version-lock cloudflared too aggressively

Pin a recent-stable version (e.g., `cloudflare/cloudflared:2024.x.x`), not `:latest`. But allow it to be overridden per-tunnel via settings — user may want to test a newer version on one tunnel first.

### Don't assume Docker networks exist

When attaching a sidecar to a target's network, the network may have been removed if the project was torn down. Always `docker.networks.get()` first, fall back to inspecting from container attrs, and surface a clear error if the target's network is gone.

### Don't batch DB writes that span CF/Docker operations

If you're doing "create CF tunnel, create sidecar, write DB row" in one transaction, you're going to have bad time on failures. Pattern: write DB row first with `status='provisioning'`, do CF/Docker operations outside the transaction, then update DB row to `status='active'` or `status='error'`. Keeps DB consistent with "what we tried to do" and surfaces mid-operation crashes.

## Coding Style

### Python
- Ruff format (line length 100)
- Ruff check with `select = ["E", "F", "I", "UP", "B", "SIM", "ASYNC", "S"]`
- mypy strict mode
- Prefer `match` over if/elif chains for discriminated unions
- Prefer explicit keyword args over positional for anything with >2 args
- No `print()` in app code; only `logger.X()` or `structlog`
- Use `asyncio.TaskGroup` over `asyncio.gather` when available (3.11+)
- Use `asyncio.timeout()` context for timeouts (not `asyncio.wait_for`)

### TypeScript
- Prettier
- ESLint with `@typescript-eslint/strict-type-checked`
- No `any`
- No non-null assertion (`!`) unless commented
- `const` functions preferred over `function` declarations for components
- Tailwind class ordering via prettier plugin

### Git
- Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`
- PR titles match commit messages
- Squash-merge default; preserve clean main history

## What to Ask For Clarification On

If you encounter any of these while implementing, pause and ask:

- **Breaking API change to CF** that invalidates the approach in doc 03
- **Deadlock or race condition** that can't be resolved with existing locking model
- **Fundamental security tradeoff** not addressed in doc 08
- **Missing dependency or capability** on target deployment platforms
- **User-facing flow that feels wrong** and the docs don't explicitly cover

For everything else: the doc has a reasonable opinion. Implement what's documented. If you disagree after implementing, flag it for a follow-up.

## Useful Helpers to Build Early

Build these once, use everywhere:

```python
# app/core/async_utils.py
class EntityLockRegistry:
    """Per-entity-id async locks. Prevents concurrent ops on same tunnel."""
    def __init__(self):
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()
    
    async def acquire(self, entity_type: str, entity_id: int) -> asyncio.Lock:
        async with self._registry_lock:
            key = (entity_type, entity_id)
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

# Usage:
lock = await lock_registry.acquire("tunnel", tunnel_id)
async with lock:
    await rotate_tunnel(tunnel_id)
```

```python
# app/core/audit.py
@asynccontextmanager
async def audit_context(
    db: AsyncSession,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    before: dict | None = None,
):
    class Ctx:
        def __init__(self):
            self.entity_id = entity_id
            self.after = None
        def set_entity_id(self, eid): self.entity_id = eid
        def set_after(self, after): self.after = after
    
    ctx = Ctx()
    request_id = getattr(request_id_context.get(), "id", None)
    try:
        yield ctx
        await db.execute(insert(AuditLog).values(
            actor=actor, action=action, entity_type=entity_type,
            entity_id=ctx.entity_id, before_json=json.dumps(before) if before else None,
            after_json=json.dumps(ctx.after) if ctx.after else None,
            status="success", request_id=request_id,
        ))
    except Exception as e:
        await db.execute(insert(AuditLog).values(
            actor=actor, action=action, entity_type=entity_type,
            entity_id=ctx.entity_id, status="failed",
            error_message=str(e), request_id=request_id,
        ))
        raise
```

```python
# app/core/ws_broker.py
class EventBroker:
    """In-process pub/sub for WebSocket fanout."""
    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
    
    async def publish(self, event: dict):
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop for slow consumer
    
    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q
    
    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)
```

## How To Know You're Done with a Phase

Each phase's acceptance criteria in [11-implementation-plan.md](11-implementation-plan.md) is a checklist. Don't declare a phase done until all items pass. If something is blocked, document it in a `KNOWN_ISSUES.md` file and leave the phase officially unfinished until resolved.

Final note: this project is intentionally designed so every phase ships something useful. At the end of Phase 4 you have a working tunnel manager for happy paths. Each subsequent phase hardens it. Don't try to build everything before you have a runnable v0.1 — the whole point of the phase structure is avoiding that trap.
