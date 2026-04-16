# 12 — Project Structure

## Top-Level Layout

```
tunnel-manager/
├── backend/                        # Python/FastAPI app
├── frontend/                       # React/Vite app
├── docker/                         # Container + compose files
├── docs/                           # These docs (copied in)
├── scripts/                        # One-off maintenance scripts
├── tests/                          # Integration + e2e tests
├── .github/
│   └── workflows/                  # CI
├── Makefile
├── pyproject.toml                  # Backend root (uv/poetry managed)
├── uv.lock                         # Dep lockfile
├── .pre-commit-config.yaml
├── .gitignore
├── .dockerignore
├── README.md
└── LICENSE
```

## Backend Layout

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app factory, lifespan
│   ├── config.py                   # Pydantic Settings
│   ├── logging_setup.py            # Structlog config with redaction
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── db.py                   # Engine, session, WAL pragmas
│   │   ├── vault.py                # AES-GCM encrypt/decrypt
│   │   ├── errors.py               # Exception hierarchy + RFC 7807
│   │   ├── labels.py               # Docker label constants
│   │   ├── comments.py             # DNS comment prefix constants
│   │   ├── security.py             # Auth, session, CSRF
│   │   └── types.py                # Shared type aliases
│   │
│   ├── models/                     # SQLModel classes
│   │   ├── __init__.py
│   │   ├── base.py                 # Mixin for timestamps, id
│   │   ├── credential.py
│   │   ├── tunnel.py
│   │   ├── route.py
│   │   ├── rotation.py
│   │   ├── dns.py                  # dns_operations, dns_backups, dns_conflicts
│   │   ├── drift.py
│   │   ├── cache.py                # zone_cache, container_cache
│   │   ├── settings.py
│   │   └── audit.py
│   │
│   ├── schemas/                    # Pydantic DTOs (request/response)
│   │   ├── __init__.py
│   │   ├── credential.py           # Create, Update, Read
│   │   ├── tunnel.py
│   │   ├── route.py
│   │   ├── container.py
│   │   ├── zone.py
│   │   ├── rotation.py
│   │   ├── drift.py
│   │   ├── dns.py
│   │   ├── audit.py
│   │   ├── settings.py
│   │   └── errors.py               # Problem details format
│   │
│   ├── clients/                    # External service adapters
│   │   ├── __init__.py
│   │   ├── cloudflare/
│   │   │   ├── __init__.py
│   │   │   ├── client.py           # Real CFClient (httpx)
│   │   │   ├── fake.py             # FakeCFClient (in-memory)
│   │   │   ├── retry.py            # Backoff + rate-limit handling
│   │   │   ├── cache.py            # Zone/account caches
│   │   │   └── models.py           # CF-specific internal types
│   │   └── docker/
│   │       ├── __init__.py
│   │       ├── client.py           # Real DockerClient wrapper
│   │       ├── fake.py             # FakeDockerClient
│   │       ├── events.py           # Event listener + dispatcher
│   │       └── helpers.py          # Common inspect/filter logic
│   │
│   ├── services/                   # Business logic
│   │   ├── __init__.py
│   │   ├── credential_service.py
│   │   ├── tunnel_service.py
│   │   ├── route_service.py
│   │   ├── dns_service.py
│   │   ├── rotation_service.py
│   │   ├── reconciliation_service.py
│   │   ├── audit_service.py
│   │   ├── container_cache_service.py
│   │   ├── zone_cache_service.py
│   │   ├── ingress_builder.py      # DB routes → CF config
│   │   ├── priority_calculator.py
│   │   ├── conflict_detector.py
│   │   ├── adopt_service.py
│   │   └── notification_service.py # Webhooks/email
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py                 # Common dependencies (auth, db session)
│   │   ├── middleware.py           # Request ID, logging, security headers
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── router.py           # Aggregates all v1 routers
│   │   │   ├── health.py
│   │   │   ├── info.py
│   │   │   ├── bootstrap.py
│   │   │   ├── auth.py
│   │   │   ├── credentials.py
│   │   │   ├── zones.py
│   │   │   ├── containers.py
│   │   │   ├── tunnels.py
│   │   │   ├── routes.py
│   │   │   ├── dns.py
│   │   │   ├── rotation.py
│   │   │   ├── reconciliation.py
│   │   │   ├── audit.py
│   │   │   ├── settings.py
│   │   │   └── ws.py               # WebSocket endpoint
│   │   └── static.py               # SPA fallthrough
│   │
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── scheduler.py            # APScheduler setup
│   │   ├── rotation_tick.py
│   │   ├── drift_scan.py
│   │   ├── health_poll.py
│   │   ├── backup_cleanup.py
│   │   ├── token_verify.py
│   │   └── event_dispatcher.py     # Routes Docker events to handlers
│   │
│   ├── realtime/
│   │   ├── __init__.py
│   │   ├── manager.py              # Connection manager for WS
│   │   └── events.py               # Event types + serialization
│   │
│   └── utils/
│       ├── __init__.py
│       ├── redaction.py            # Log message redaction
│       ├── time.py                 # datetime helpers
│       ├── strings.py              # hostname parsing, etc.
│       └── asyncio_utils.py        # Lock registry, etc.
│
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   ├── README.md
│   └── versions/
│       ├── 0001_initial_schema.py
│       └── ...                     # future migrations
├── alembic.ini
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # Fixtures: db, vault, fake clients
│   ├── unit/
│   │   ├── test_vault.py
│   │   ├── test_models.py
│   │   ├── test_priority_calculator.py
│   │   ├── test_ingress_builder.py
│   │   ├── test_conflict_detector.py
│   │   ├── test_redaction.py
│   │   └── ...
│   ├── integration/
│   │   ├── test_cf_client.py       # uses FakeCFClient + VCR
│   │   ├── test_docker_client.py   # uses testcontainers
│   │   ├── test_tunnel_service.py
│   │   ├── test_route_service.py
│   │   ├── test_rotation_service.py
│   │   ├── test_reconciliation.py
│   │   └── test_api_endpoints.py   # TestClient
│   ├── e2e/                        # Full stack tests
│   │   └── test_full_lifecycle.py
│   └── fixtures/
│       ├── cf_responses/           # VCR cassettes
│       ├── docker_attrs/           # sample container attrs
│       └── db_seeds/               # SQL seed files
│
├── pyproject.toml
└── README.md
```

## Frontend Layout

```
frontend/
├── index.html
├── vite.config.ts
├── tsconfig.json
├── tsconfig.node.json
├── tailwind.config.ts
├── postcss.config.js
├── components.json                 # shadcn/ui config
├── .eslintrc.cjs
├── .prettierrc
├── package.json
├── pnpm-lock.yaml (or package-lock.json)
│
├── public/
│   └── favicon.svg
│
├── src/
│   ├── main.tsx                    # Root render + QueryClient + Router
│   ├── App.tsx                     # Route tree
│   ├── index.css                   # Tailwind imports
│   │
│   ├── api/
│   │   ├── client.ts               # fetch wrapper
│   │   ├── errors.ts               # ApiError + 409 conflict parsing
│   │   ├── websocket.ts
│   │   ├── auth.ts
│   │   ├── bootstrap.ts
│   │   ├── credentials.ts
│   │   ├── zones.ts
│   │   ├── containers.ts
│   │   ├── tunnels.ts
│   │   ├── routes.ts
│   │   ├── dns.ts
│   │   ├── rotation.ts
│   │   ├── reconciliation.ts
│   │   ├── audit.ts
│   │   └── settings.ts
│   │
│   ├── hooks/
│   │   ├── useTunnels.ts
│   │   ├── useTunnel.ts
│   │   ├── useRoutes.ts
│   │   ├── useRoute.ts
│   │   ├── useContainers.ts
│   │   ├── useZones.ts
│   │   ├── useDriftFindings.ts
│   │   ├── useOrphans.ts
│   │   ├── useRotationEvents.ts
│   │   ├── useAuditLog.ts
│   │   ├── useSettings.ts
│   │   ├── useWebSocket.ts
│   │   └── useAuth.ts
│   │
│   ├── types/
│   │   ├── api.ts                  # Matches backend Pydantic schemas
│   │   └── ws.ts                   # WS event types
│   │
│   ├── lib/
│   │   ├── utils.ts                # cn(), small helpers
│   │   ├── format.ts               # dates, durations, bytes
│   │   ├── zod-schemas.ts          # Shared validation
│   │   ├── toast.ts
│   │   └── error-handler.ts
│   │
│   ├── components/
│   │   ├── ui/                     # shadcn/ui primitives
│   │   │   ├── button.tsx
│   │   │   ├── dialog.tsx
│   │   │   ├── input.tsx
│   │   │   ├── badge.tsx
│   │   │   ├── tabs.tsx
│   │   │   ├── toast.tsx
│   │   │   └── ...
│   │   ├── common/
│   │   │   ├── StatusBadge.tsx
│   │   │   ├── DateDisplay.tsx
│   │   │   ├── RelativeTime.tsx
│   │   │   ├── TokenDisplay.tsx
│   │   │   ├── CopyButton.tsx
│   │   │   ├── ConfirmDialog.tsx
│   │   │   ├── ConflictDialog.tsx
│   │   │   ├── EmptyState.tsx
│   │   │   ├── ErrorState.tsx
│   │   │   └── LoadingSpinner.tsx
│   │   ├── layout/
│   │   │   ├── AppShell.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   ├── Header.tsx
│   │   │   └── ThemeToggle.tsx
│   │   ├── bootstrap/
│   │   │   ├── BootstrapWizard.tsx
│   │   │   ├── TokenStep.tsx
│   │   │   ├── AccountStep.tsx
│   │   │   ├── ImportStep.tsx
│   │   │   └── ReviewStep.tsx
│   │   ├── tunnels/
│   │   │   ├── TunnelList.tsx
│   │   │   ├── TunnelCard.tsx
│   │   │   ├── TunnelGroup.tsx     # Groups by project
│   │   │   ├── TunnelDetail.tsx
│   │   │   ├── TunnelHealthPanel.tsx
│   │   │   ├── CreateTunnelModal.tsx
│   │   │   ├── EditTunnelModal.tsx
│   │   │   ├── RotationHistoryTable.tsx
│   │   │   └── SidecarLogsPane.tsx
│   │   ├── routes/
│   │   │   ├── RouteList.tsx
│   │   │   ├── RouteRow.tsx
│   │   │   ├── AddRouteModal.tsx
│   │   │   ├── EditRouteModal.tsx
│   │   │   ├── RouteReorderList.tsx
│   │   │   ├── TestMatchWidget.tsx
│   │   │   └── OriginOptionsForm.tsx
│   │   ├── containers/
│   │   │   ├── ContainerList.tsx
│   │   │   ├── ContainerGroup.tsx
│   │   │   └── ContainerDetail.tsx
│   │   ├── drift/
│   │   │   ├── DriftList.tsx
│   │   │   ├── DriftRow.tsx
│   │   │   ├── DiffView.tsx
│   │   │   └── ResolveActions.tsx
│   │   ├── orphans/
│   │   │   ├── OrphanDNSList.tsx
│   │   │   ├── OrphanTunnelList.tsx
│   │   │   └── OrphanSidecarList.tsx
│   │   ├── credentials/
│   │   │   ├── CredentialList.tsx
│   │   │   └── CredentialRotationModal.tsx
│   │   ├── audit/
│   │   │   ├── AuditTable.tsx
│   │   │   └── AuditRowExpanded.tsx
│   │   └── settings/
│   │       ├── GeneralSettings.tsx
│   │       ├── PolicySettings.tsx
│   │       ├── RotationSettings.tsx
│   │       ├── NotificationSettings.tsx
│   │       └── BackupSettings.tsx
│   │
│   ├── pages/
│   │   ├── LoginPage.tsx
│   │   ├── BootstrapPage.tsx
│   │   ├── Dashboard.tsx
│   │   ├── TunnelsPage.tsx
│   │   ├── TunnelDetailPage.tsx
│   │   ├── RoutesPage.tsx
│   │   ├── RouteDetailPage.tsx
│   │   ├── ContainersPage.tsx
│   │   ├── CredentialsPage.tsx
│   │   ├── ZonesPage.tsx
│   │   ├── DriftPage.tsx
│   │   ├── OrphansPage.tsx
│   │   ├── AuditPage.tsx
│   │   ├── SettingsPage.tsx
│   │   └── NotFoundPage.tsx
│   │
│   ├── guards/
│   │   ├── RequireAuth.tsx
│   │   └── RequireBootstrap.tsx
│   │
│   └── providers/
│       ├── QueryProvider.tsx
│       ├── ThemeProvider.tsx
│       └── WebSocketProvider.tsx
│
├── tests/
│   ├── unit/                       # Vitest
│   └── e2e/                        # Playwright
│       ├── bootstrap.spec.ts
│       ├── tunnel-crud.spec.ts
│       ├── route-crud.spec.ts
│       ├── conflict-resolution.spec.ts
│       ├── rotation.spec.ts
│       └── drift.spec.ts
│
└── README.md
```

## Docker Layout

```
docker/
├── Dockerfile                      # Multi-stage: frontend build → backend + static
├── Dockerfile.dev                  # Dev image with hot reload
├── compose.dev.yml                 # Local development compose
├── compose.prod.yml                # Production compose (reference impl)
├── entrypoint.sh                   # Runs migrations then starts app
└── nginx.conf                      # Optional reverse proxy config
```

## Dockerfile (Multi-Stage Sketch)

```dockerfile
# Stage 1: Build frontend
FROM node:20-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# Stage 2: Python deps
FROM python:3.12-slim AS pydeps
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv pip install --system --no-cache -r pyproject.toml

# Stage 3: Runtime
FROM python:3.12-slim AS runtime
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl sqlite3 \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 1001 --home /app --shell /sbin/nologin tm

WORKDIR /app
COPY --from=pydeps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=pydeps /usr/local/bin /usr/local/bin
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY --from=frontend /app/dist ./app/static
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R tm:tm /app

USER tm
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8088/api/v1/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
```

## Scripts Layout

```
scripts/
├── generate-master-key.sh          # Wraps openssl rand
├── nightly-backup.sh               # Cron-friendly DB backup
├── restore-from-litestream.sh      # DR helper
├── migrate-from-compose.sh         # Interactive migration from per-project cloudflared
└── redact-logs-audit.py            # Scans log fixtures for leaked tokens
```

## Test Layout Expansion

```
tests/
├── e2e/                            # End-to-end against real stack
│   ├── conftest.py                 # Spin up full docker-compose for test
│   ├── test_bootstrap_flow.py
│   ├── test_tunnel_lifecycle.py
│   ├── test_route_lifecycle.py
│   ├── test_rotation_with_traffic.py
│   └── test_migration_from_compose.py
└── performance/
    ├── test_bulk_rotation.py
    └── test_drift_scan_at_scale.py
```

## Configuration Files

### `pyproject.toml` (key sections)

```toml
[project]
name = "tunnel-manager"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlmodel>=0.0.22",
    "alembic>=1.13",
    "aiosqlite>=0.20",
    "pydantic>=2.9",
    "pydantic-settings>=2.5",
    "httpx>=0.27",
    "docker>=7.1",
    "cryptography>=43",
    "apscheduler>=3.10",
    "tenacity>=9",
    "structlog>=24",
    "argon2-cffi>=23",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5",
    "vcrpy>=6",
    "testcontainers>=4",
    "ruff>=0.6",
    "mypy>=1.11",
    "pre-commit>=3",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ASYNC", "S"]
ignore = ["S101"]  # assert ok in tests

[tool.mypy]
python_version = "3.12"
strict = true
warn_unused_ignores = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

## File-Level Conventions

- **One class per file where practical.** Exceptions: small helper classes used only inside one module.
- **Imports ordered by ruff/isort:** stdlib → third-party → first-party → local relative.
- **No star imports.** Explicit re-exports in `__init__.py` where needed.
- **Async at API and service layer. Sync only inside client wrappers where SDK is sync.**
- **Type hints required on all public functions.** mypy strict mode.
- **Docstrings: Google style for public functions/classes.**
- **Tests mirror source structure:** `app/services/route_service.py` → `tests/unit/services/test_route_service.py`.

## Dependency Direction Rules

Enforced by import-linter (configured in `pyproject.toml`):

```
api layer     →  services layer  →  clients + core + models
workers       →  services + clients
realtime      →  services + core
clients       →  core
services      →  clients + models + core (no api, no workers)
models        →  core only
core          →  nothing
```

Violations fail CI.

## Naming Conventions

- **Python modules/packages:** `snake_case`
- **Python classes:** `PascalCase`
- **Python functions/methods/variables:** `snake_case`
- **Python constants:** `UPPER_SNAKE`
- **TypeScript files:** `PascalCase.tsx` for components, `camelCase.ts` for hooks/utils
- **TypeScript types/interfaces:** `PascalCase`
- **TypeScript variables/functions:** `camelCase`
- **API endpoints:** `kebab-case` resource names, e.g., `/api/v1/rotation-events`
- **Docker labels:** `tunnel-manager.snake.case=value`
- **DB tables:** `snake_case`, plural
- **DB columns:** `snake_case`
