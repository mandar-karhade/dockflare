# 01 — System Architecture

## Design Principles

1. **Socket-native, not port-exposed.** All Docker operations go through the local Unix socket, filtered by `docker-socket-proxy`. The manager never connects to Docker over TCP.
2. **Per-container tunnel isolation.** Each target container gets its own `cloudflared` sidecar. Failure domains stay narrow.
3. **Database as source of truth.** All tunnel/route/DNS state is authoritative in the local SQLite DB. Cloudflare and Docker are reconciled *to* the DB, with drift detection surfacing divergence.
4. **Compose-aware identity.** Routes bind to `(project, service)` pairs from Docker Compose labels, not container IDs. Survives `down`/`up` cycles.
5. **Defense in depth for secrets.** Tokens are AES-GCM encrypted with a master key loaded from a Docker secret. Even DB filesystem access alone doesn't leak tokens.
6. **Eventually consistent, not transactional.** Operations against Cloudflare and Docker can partially fail. Every mutation records intent first, then reconciles. Failed operations are surfaced for retry, not silently swallowed.

## Component Topology

```
┌───────────────────────────────────────────────────────────────┐
│  Manager Container                                             │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  FastAPI Application                                      │ │
│  │                                                            │ │
│  │  ┌─────────────────┐  ┌─────────────────┐                │ │
│  │  │ HTTP Layer      │  │ WebSocket Layer │                │ │
│  │  │ - REST routes   │  │ - Live events   │                │ │
│  │  │ - Static files  │  │ - Status push   │                │ │
│  │  └────────┬────────┘  └────────┬────────┘                │ │
│  │           │                     │                          │ │
│  │  ┌────────▼─────────────────────▼────────┐                │ │
│  │  │ Service Layer                           │                │ │
│  │  │ ┌─────────────┐  ┌──────────────────┐  │                │ │
│  │  │ │ Tunnel Svc  │  │ Route Svc        │  │                │ │
│  │  │ └─────────────┘  └──────────────────┘  │                │ │
│  │  │ ┌─────────────┐  ┌──────────────────┐  │                │ │
│  │  │ │ Rotation Svc│  │ Reconciliation   │  │                │ │
│  │  │ └─────────────┘  └──────────────────┘  │                │ │
│  │  │ ┌─────────────┐  ┌──────────────────┐  │                │ │
│  │  │ │ Vault Svc   │  │ DNS Svc          │  │                │ │
│  │  │ └─────────────┘  └──────────────────┘  │                │ │
│  │  └────┬──────────────────────┬─────────────┘                │ │
│  │       │                      │                               │ │
│  │  ┌────▼──────┐  ┌────────────▼──────┐  ┌────────────────┐ │ │
│  │  │ CF Client │  │ Docker Client     │  │ DB (SQLAlchemy)│ │ │
│  │  │ (httpx)   │  │ (docker-py)       │  │ SQLite WAL     │ │ │
│  │  └───────────┘  └───────────────────┘  └────────────────┘ │ │
│  │                                                            │ │
│  │  ┌──────────────────────────────────────────────────────┐ │ │
│  │  │ Background Workers (APScheduler)                      │ │ │
│  │  │ - Docker event listener (always-on)                   │ │ │
│  │  │ - Rotation scheduler (15min interval)                 │ │ │
│  │  │ - Drift detection (1hr interval)                      │ │ │
│  │  │ - Health poller (30s interval)                        │ │ │
│  │  │ - Backup cleanup (daily)                              │ │ │
│  │  └──────────────────────────────────────────────────────┘ │ │
│  └──────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
```

## Service Layer Responsibilities

### TunnelService
- Create/delete CF tunnels
- Fetch tunnel tokens from CF API
- Maintain tunnel <-> cloudflared sidecar lifecycle
- Track tunnel health via CF connection API

### RouteService
- CRUD operations for routes
- Trigger ingress rebuild on mutation
- Pre-flight conflict checks
- Manage DNS record creation per route

### RotationService
- Scheduled + manual token rotation
- Rolling restart via overlap technique
- Rotation history and audit
- Handle `force_recreate` destructive path

### ReconciliationService
- Periodic drift detection
- Orphan record scanning
- Adopt-existing flow for migration
- Conflict resolution state machine

### VaultService
- AES-GCM encrypt/decrypt for all secrets
- Master key loading from Docker secret
- Key rotation for master key

### DNSService
- Zone resolution (hostname → zone_id)
- CNAME create/update/delete with safety prefix
- DNS backup-before-replace
- Zone enumeration caching

### CFClient
- Thin HTTP wrapper over Cloudflare API
- Rate limit awareness
- Retry with exponential backoff
- Response caching for read-only endpoints (zones, accounts)

### DockerClient
- Container list/inspect/create/remove
- Network connect/disconnect
- Event stream subscription
- Label-based container lookup

## Data Flow: Adding a New Route

```
User clicks "Add Route" in GUI
         │
         ▼
POST /api/v1/routes {tunnel_id, hostname, target, port, ...}
         │
         ▼
RouteService.create(params)
         │
         ├──► DNSService.resolve_zone(hostname) ──► CFClient.zones.list()
         │                                                  │
         │                                                  ▼
         │                                          returns zone_id
         │
         ├──► DNSService.check_conflict(hostname, zone_id)
         │                     │
         │                     ▼
         │             CFClient.dns.records.list(name=hostname)
         │                     │
         │                     ▼
         │             Returns conflict_status
         │
         ├──► [if conflict] → 409 Conflict response with resolution options
         │
         ├──► DockerClient.find_container_by_compose(project, service)
         │                     │
         │                     ▼
         │             Returns target container + networks
         │
         ├──► DockerClient.attach_network(cloudflared_id, target_network)
         │    (if cloudflared not already on target's network)
         │
         ├──► DB: INSERT route (status='provisioning')
         │
         ├──► RouteService.sync_tunnel_ingress(tunnel_id)
         │         │
         │         ▼
         │    Build full ingress list from DB, push to CF
         │    CFClient.cfd_tunnel.configurations.update(...)
         │
         ├──► DNSService.create_record(zone_id, hostname, cname_target)
         │         │
         │         ▼
         │    CFClient.dns.records.create(..., comment='tunnel-manager:{route_id}')
         │
         ├──► DB: UPDATE route SET status='active', cf_dns_record_id=...
         │
         ▼
201 Created + route details
         │
         ▼
WebSocket push to all connected GUI clients
```

## Data Flow: Docker Event Handling

```
Docker daemon emits event (container start/stop/destroy)
         │
         ▼
Event listener (background task) receives event
         │
         ├──► Filter: only care about containers with
         │           - com.docker.compose.project label
         │           - or tunnel-manager.managed label
         │
         ▼
Event router dispatches by type:

  container.start
    └──► Check: is there a route bound to this (project,service)?
          ├── yes: ensure cloudflared sidecar exists + is running
          └── no:  ignore

  container.die
    └──► Check: is this a managed cloudflared?
          ├── yes: mark associated routes as 'degraded', trigger restart
          └── no: check if it's a route target; if yes, mark routes 'target_down'

  container.destroy
    └──► If compose target: mark routes as 'orphaned', stop sidecar
    └──► If cloudflared: remove from DB, orphan routes remain

  network.connect / network.disconnect
    └──► Update DB network attachment tracking

All events also broadcast via WebSocket for live GUI updates.
```

## Threading / Concurrency Model

FastAPI runs on `uvicorn` with async handlers. Background jobs use APScheduler's async executor. Docker event listener is a long-running async task started on app startup.

- **HTTP handlers:** fully async, non-blocking
- **Docker SDK calls:** `docker-py` is sync; wrap in `asyncio.to_thread()` or `run_in_executor()`
- **CF API calls:** use `httpx.AsyncClient` for native async
- **DB operations:** use SQLAlchemy async session (`asyncpg` style via `aiosqlite`)
- **Background jobs:** APScheduler with `AsyncIOExecutor`

Rule: no sync blocking calls in async handlers. Everything either awaits or threads.

## Network Topology Inside Docker

```
Networks:
├── tunnel-manager_internal (isolated)
│   ├── socket-proxy
│   ├── manager
│   └── litestream
│
├── project-a_default (managed by project-a compose)
│   ├── project-a_app
│   └── cftunnel-project-a-app   ← spawned by manager
│
├── project-b_default (managed by project-b compose)
│   ├── project-b_api
│   ├── project-b_db
│   └── cftunnel-project-b-api   ← spawned by manager
│
└── (more as needed)
```

Key observation: each `cftunnel-*` container is attached ONLY to its target's network. The manager itself is NOT attached to project networks — it operates on them via the Docker API only.

Cross-project routing (e.g., tunnel for project-a also routes to a container in project-b) is supported by attaching that tunnel's cloudflared to project-b's network on demand. The GUI flags this explicitly since it affects network isolation assumptions.

## Failure Domains

| Failure | Blast Radius | Recovery |
|---------|--------------|----------|
| Manager container crashes | No traffic impact (cloudflared keeps running) | Restart manager; reconciles state on boot |
| Single cloudflared crashes | Only its tunnel's routes go down | Auto-restart via `restart: unless-stopped` |
| SQLite DB corrupts | Manager can't make changes, existing routes keep working | Restore from litestream backup |
| CF API outage | Manager can't make changes, existing routes keep working | Wait for CF recovery |
| VPS dies | All tunnels down | New VPS + litestream restore + `docker compose up` |
| Master key lost | Cannot decrypt tokens, must re-add all via GUI | Keep key backup; fall back to CF dashboard re-issue |
| CF API token revoked | All operations fail, existing traffic still flows | Generate new token, paste in GUI |

## Startup Sequence

```
1. Load config from environment
2. Load master encryption key from /run/secrets/master_key
3. Initialize DB (run Alembic migrations)
4. Verify DB integrity (check for incomplete operations)
5. Connect to Docker via socket-proxy
6. Verify CF API token if present (via /user/tokens/verify)
7. Load existing tunnels/routes from DB
8. Reconcile on startup:
   a. For each expected cloudflared sidecar, ensure it's running
   b. For each tracked container, update current container_id
   c. Mark orphaned anything missing
9. Start background jobs (scheduler, event listener, health poller)
10. Bind HTTP + WebSocket, accept requests
```

If step 5 fails, manager still starts in read-only mode so the GUI shows the error. If step 6 fails (no token / invalid token), GUI shows onboarding screen.

## Shutdown Sequence

```
1. Stop accepting new HTTP requests
2. Wait for in-flight requests to complete (30s timeout)
3. Stop background jobs (scheduler.shutdown(wait=True))
4. Close Docker event listener
5. Flush any pending DB writes
6. Close DB connections
7. Exit
```

Important: shutdown does NOT stop cloudflared sidecars. They remain running — traffic keeps flowing during manager restarts/upgrades.
