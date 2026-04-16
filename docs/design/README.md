# Cloudflare Tunnel Manager

A self-hosted management plane for Cloudflare Tunnels that orchestrates per-container `cloudflared` sidecars via the Docker socket, eliminates `.env`-scattered token management, and replaces the Cloudflare dashboard workflow for tunnel and DNS operations.

## Why This Exists

Current state for multi-project Docker VPS users: every project's `docker-compose.yml` carries a `cloudflared` service, every project's `.env` carries a `CF_TUNNEL_TOKEN`, every new hostname means editing ingress YAML, restarting stacks, and manually creating DNS records in the Cloudflare dashboard.

Target state: project compose files contain only the project. One management container orchestrates all tunnel sidecars, stores tokens encrypted in a central vault, and exposes a GUI for tunnel/route/DNS operations against the Cloudflare API.

## Core Capabilities

- **Per-container tunnel isolation.** Each tunneled container gets a dedicated `cloudflared` sidecar on its own Docker network. Failure in one project's tunnel never affects another.
- **Socket-native Docker control.** Spawns, stops, attaches, and monitors containers via `/var/run/docker.sock` (behind a filtering proxy). No exposed Docker TCP ports.
- **Zero host ports per tunnel.** Only the manager's GUI port is exposed; every tunneled service is reached via the outbound cloudflared connection to Cloudflare edge.
- **Full Cloudflare dashboard replacement** for tunnel operations. Creates tunnels, fetches tokens, resolves zones, manages DNS CNAMEs, and updates ingress — all via one API token with minimal scopes.
- **Multi-route per tunnel.** One tunnel can serve many hostnames via ordered ingress rules, with priority-based matching, path regex support, and per-rule origin overrides.
- **Compose-aware identity.** Routes bind to `(compose_project, compose_service)` pairs that survive `docker compose down`/`up` cycles, not ephemeral container IDs.
- **Encrypted token vault with rotation.** AES-GCM-encrypted at rest, rotation scheduler with rolling-restart (near-zero downtime), full audit trail.
- **Drift detection and reconciliation.** Periodic scans compare DB state against Cloudflare reality; surfaces external edits for user-driven resolution.
- **Import/adopt existing setups.** Scan an account for existing tunnels and DNS records, adopt them into the manager's DB — no manual re-entry during migration.
- **Litestream-backed disaster recovery.** Continuous SQLite replication to R2/S3; restore full state on a new VPS by copying one DB file.

## Document Map

| File | Purpose |
|------|---------|
| [01-architecture.md](01-architecture.md) | System architecture, component breakdown, data flow diagrams |
| [02-data-model.md](02-data-model.md) | Complete database schema, migrations, encryption design |
| [03-cloudflare-integration.md](03-cloudflare-integration.md) | CF API usage, required scopes, tunnel/DNS/ingress workflows |
| [04-docker-integration.md](04-docker-integration.md) | Docker socket usage, event loop, sidecar lifecycle management |
| [05-api-specification.md](05-api-specification.md) | REST API endpoints, request/response schemas |
| [06-rotation-engine.md](06-rotation-engine.md) | Token rotation design, rolling restart mechanics |
| [07-reconciliation.md](07-reconciliation.md) | Drift detection, conflict resolution, orphan handling |
| [08-security.md](08-security.md) | Threat model, encryption, secret handling, hardening |
| [09-frontend-spec.md](09-frontend-spec.md) | GUI views, component structure, state management |
| [10-deployment.md](10-deployment.md) | Docker Compose, secrets setup, first-run onboarding |
| [11-implementation-plan.md](11-implementation-plan.md) | Phased build order, milestones, acceptance criteria |
| [12-project-structure.md](12-project-structure.md) | Directory layout, file-level organization |
| [13-testing-strategy.md](13-testing-strategy.md) | Unit, integration, end-to-end test approach |
| [14-claude-code-guide.md](14-claude-code-guide.md) | Instructions for Claude Code agents implementing this project |

## Tech Stack

- **Backend:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy + SQLModel, Alembic, APScheduler, `httpx`, `docker` SDK, `cryptography`
- **Database:** SQLite with WAL mode, Litestream replication to R2
- **Frontend:** React 18+, TypeScript, TanStack Query, shadcn/ui, Tailwind CSS, Vite
- **Deployment:** Docker Compose, `tecnativa/docker-socket-proxy`, Docker secrets
- **Tunneling:** `cloudflare/cloudflared` (token-based tunnels, remotely-managed config)

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  VPS Host                                                    │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  tunnel-manager network (isolated)                    │   │
│  │                                                        │   │
│  │  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  │   │
│  │  │ GUI        │  │ Manager API  │  │ Socket Proxy │  │   │
│  │  │ React/Vite │──│ FastAPI      │──│ Filtered     │  │   │
│  │  │ :8088      │  │ SQLite       │  │ Docker API   │  │   │
│  │  └────────────┘  └──────┬───────┘  └──────┬───────┘  │   │
│  │                         │                  │          │   │
│  │  ┌──────────────┐       │                  │          │   │
│  │  │ Litestream   │◄──────┘                  │          │   │
│  │  │ → R2 backup  │                          │          │   │
│  │  └──────────────┘                          │          │   │
│  └────────────────────────────────────────────┼──────────┘   │
│                                                │              │
│                                                ▼              │
│                                    /var/run/docker.sock (ro) │
│                                                │              │
│  ┌─────────────────────┐  ┌─────────────────────▼──────────┐ │
│  │ project-a network   │  │ Docker Daemon                  │ │
│  │ ┌────┐  ┌──────────┐│  └────────────────────────────────┘ │
│  │ │app │◄─│cftunnel-a││                                     │
│  │ └────┘  └──────────┘│                                     │
│  └─────────────────────┘                                     │
│                                                               │
│  ┌─────────────────────┐                                     │
│  │ project-b network   │                                     │
│  │ ┌────┐  ┌──────────┐│                                     │
│  │ │app │◄─│cftunnel-b││                                     │
│  │ └────┘  └──────────┘│                                     │
│  └─────────────────────┘                                     │
└──────────────────────────────────────────────────────────────┘
                                │
                                ▼  (outbound QUIC/HTTP2 only)
                        Cloudflare Edge
                                │
                                ▼
                           Public Internet
```

## Getting Started (for implementation)

1. Read [11-implementation-plan.md](11-implementation-plan.md) for phased build order.
2. Read [14-claude-code-guide.md](14-claude-code-guide.md) for Claude Code-specific guidance.
3. Start from [12-project-structure.md](12-project-structure.md) to scaffold directories.
4. Work phase-by-phase; each phase has acceptance criteria.
