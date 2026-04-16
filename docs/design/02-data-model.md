# 02 — Data Model

## Overview

Single SQLite database (`tm.db`) in WAL mode. All schema evolution via Alembic migrations. All secrets encrypted at rest with AES-GCM using a master key loaded from a Docker secret.

Encryption scheme for secret columns:
- Cipher: AES-256-GCM
- Key: 32-byte master key from `/run/secrets/master_key`
- Nonce: random 12 bytes per encryption, prepended to ciphertext
- Auth tag: 16 bytes, appended by GCM
- Storage format: `nonce (12) || ciphertext || tag (16)` — BLOB column

## Schema

### `cf_credentials`

Stores the Cloudflare API token used by the manager to talk to CF.

```sql
CREATE TABLE cf_credentials (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,              -- user-provided label
    account_id              TEXT NOT NULL,              -- CF account ID
    account_name            TEXT,                        -- cached for display
    token_encrypted         BLOB NOT NULL,              -- AES-GCM encrypted
    token_last_four         TEXT NOT NULL,              -- for UI identification
    token_fingerprint       TEXT NOT NULL UNIQUE,       -- sha256 of plaintext, for dedup
    scopes_json             TEXT,                        -- verified scopes snapshot
    expires_at              TIMESTAMP,                   -- from CF, if TTL set
    is_active               BOOLEAN NOT NULL DEFAULT 1,
    last_verified_at        TIMESTAMP,
    last_verification_status TEXT,                      -- 'valid'|'invalid'|'expired'
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cf_credentials_active ON cf_credentials(is_active);
```

Only one row should have `is_active=1` at a time. Rotation flow: add new row with `is_active=0`, verify it works, flip flags atomically.

### `tunnels`

One row per Cloudflare tunnel managed by the system.

```sql
CREATE TABLE tunnels (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    cf_tunnel_id            TEXT NOT NULL UNIQUE,       -- UUID from CF
    cf_tunnel_name          TEXT NOT NULL,              -- human-readable name
    cf_credential_id        INTEGER NOT NULL,           -- which CF token owns it
    account_id              TEXT NOT NULL,
    token_encrypted         BLOB NOT NULL,              -- tunnel token, AES-GCM
    token_last_four         TEXT NOT NULL,
    token_fetched_at        TIMESTAMP NOT NULL,
    token_deployed_at       TIMESTAMP,                   -- when sidecar last restarted
    -- Primary target (informational; routes have their own targets)
    primary_compose_project TEXT,
    primary_compose_service TEXT,
    cloudflared_container_id TEXT,                       -- current sidecar container ID
    cloudflared_image       TEXT DEFAULT 'cloudflare/cloudflared:latest',
    -- Rotation
    rotation_policy         TEXT NOT NULL DEFAULT 'manual',  -- 'manual'|'7d'|'30d'|'90d'|custom cron
    next_rotation_due       TIMESTAMP,
    last_rotation_at        TIMESTAMP,
    last_rotation_status    TEXT,                        -- 'success'|'noop'|'failed:reason'
    -- Lifecycle
    status                  TEXT NOT NULL DEFAULT 'active', -- 'active'|'disabled'|'error'
    error_message           TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cf_credential_id) REFERENCES cf_credentials(id)
);

CREATE INDEX idx_tunnels_status ON tunnels(status);
CREATE INDEX idx_tunnels_rotation_due ON tunnels(next_rotation_due)
    WHERE rotation_policy != 'manual';
CREATE INDEX idx_tunnels_compose_target ON tunnels(primary_compose_project, primary_compose_service);
```

### `routes`

One row per hostname served by a tunnel. Multiple routes per tunnel allowed.

```sql
CREATE TABLE routes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tunnel_id               INTEGER NOT NULL,
    -- Matching criteria
    hostname                TEXT NOT NULL,              -- exact or wildcard (*.foo.com)
    path_regex              TEXT,                        -- optional path matcher
    priority                INTEGER NOT NULL,           -- ingress order, lower = earlier
    -- Destination
    target_compose_project  TEXT,
    target_compose_service  TEXT,
    target_container_name   TEXT,                        -- fallback for non-compose
    target_scheme           TEXT NOT NULL DEFAULT 'http', -- http|https|tcp|ssh|rdp|unix
    target_port             INTEGER,                     -- null for unix sockets
    target_unix_socket_path TEXT,                        -- when scheme=unix
    target_path_prefix      TEXT,                        -- appended to service URL
    -- Origin request options (cloudflared origin_request block)
    no_tls_verify           BOOLEAN NOT NULL DEFAULT 0,
    http_host_header        TEXT,
    origin_server_name      TEXT,
    connect_timeout_seconds INTEGER DEFAULT 30,
    tcp_keep_alive_seconds  INTEGER,
    http2_origin            BOOLEAN NOT NULL DEFAULT 0,
    -- DNS
    zone_id                 TEXT NOT NULL,
    zone_name               TEXT NOT NULL,              -- cached for display
    cf_dns_record_id        TEXT,                        -- null until DNS created
    dns_proxied             BOOLEAN NOT NULL DEFAULT 1,
    -- Lifecycle
    enabled                 BOOLEAN NOT NULL DEFAULT 1,
    status                  TEXT NOT NULL DEFAULT 'provisioning',
    -- Possible statuses: provisioning, active, disabled, orphaned, error, target_down
    status_detail           TEXT,
    last_healthy_at         TIMESTAMP,
    last_error_at           TIMESTAMP,
    last_error_message      TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tunnel_id) REFERENCES tunnels(id) ON DELETE CASCADE,
    -- Unique hostname+path combination per tunnel
    UNIQUE (tunnel_id, hostname, path_regex)
);

CREATE INDEX idx_routes_tunnel ON routes(tunnel_id);
CREATE INDEX idx_routes_hostname ON routes(hostname);
CREATE INDEX idx_routes_status ON routes(status);
CREATE INDEX idx_routes_target ON routes(target_compose_project, target_compose_service);
CREATE INDEX idx_routes_priority ON routes(tunnel_id, priority);
```

Constraint enforced in application code (not DB): globally unique hostnames across all routes in same CF account. DB allows duplicates across tunnels for migration/adopt scenarios; app-layer validation rejects.

### `rotation_events`

Audit trail for all token rotations (tunnel tokens and CF API credentials).

```sql
CREATE TABLE rotation_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type             TEXT NOT NULL,              -- 'tunnel'|'cf_credential'|'master_key'
    entity_id               INTEGER NOT NULL,
    triggered_by            TEXT NOT NULL,              -- 'scheduled'|'manual'|'alert'|'force_recreate'
    started_at              TIMESTAMP NOT NULL,
    completed_at            TIMESTAMP,
    status                  TEXT NOT NULL,              -- 'in_progress'|'success'|'noop'|'failed'|'rolled_back'
    old_token_last_four     TEXT,
    new_token_last_four     TEXT,
    downtime_seconds        REAL,
    error_message           TEXT,
    details_json            TEXT,                        -- extra context
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_rotation_events_entity ON rotation_events(entity_type, entity_id);
CREATE INDEX idx_rotation_events_status ON rotation_events(status);
CREATE INDEX idx_rotation_events_time ON rotation_events(started_at DESC);
```

### `dns_operations`

Audit trail for all DNS mutations.

```sql
CREATE TABLE dns_operations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    operation               TEXT NOT NULL,              -- 'create'|'update'|'delete'
    zone_id                 TEXT NOT NULL,
    cf_record_id            TEXT,
    hostname                TEXT NOT NULL,
    record_type             TEXT,                        -- 'CNAME'|'A'|...
    old_content             TEXT,                        -- for updates/deletes
    new_content             TEXT,                        -- for creates/updates
    old_proxied             BOOLEAN,
    new_proxied             BOOLEAN,
    triggered_by            TEXT NOT NULL,              -- 'route:{id}'|'manual'|'cleanup'|'conflict_resolution'
    route_id                INTEGER,
    status                  TEXT NOT NULL,              -- 'success'|'failed'
    error_message           TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE SET NULL
);

CREATE INDEX idx_dns_operations_hostname ON dns_operations(hostname);
CREATE INDEX idx_dns_operations_time ON dns_operations(created_at DESC);
CREATE INDEX idx_dns_operations_route ON dns_operations(route_id);
```

### `dns_backups`

Snapshots of DNS records that were replaced by manager operations. Enables one-click restore.

```sql
CREATE TABLE dns_backups (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_id                 TEXT NOT NULL,
    hostname                TEXT NOT NULL,
    record_type             TEXT NOT NULL,              -- original type (A, AAAA, CNAME, ...)
    record_content          TEXT NOT NULL,
    record_ttl              INTEGER,
    record_proxied          BOOLEAN,
    record_comment          TEXT,
    backed_up_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason                  TEXT NOT NULL,              -- 'replaced_by_route'|'manual_before_delete'
    related_route_id        INTEGER,
    restored_at             TIMESTAMP,
    retention_until         TIMESTAMP,                   -- auto-purge cutoff
    FOREIGN KEY (related_route_id) REFERENCES routes(id) ON DELETE SET NULL
);

CREATE INDEX idx_dns_backups_hostname ON dns_backups(hostname);
CREATE INDEX idx_dns_backups_retention ON dns_backups(retention_until);
```

Default retention: 90 days. Background job purges expired entries.

### `dns_conflicts`

Record of conflict situations and their resolutions (for audit).

```sql
CREATE TABLE dns_conflicts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname                TEXT NOT NULL,
    zone_id                 TEXT NOT NULL,
    conflict_type           TEXT NOT NULL,
    -- 'external_record'|'orphan_owned'|'drift_content'|'drift_missing'|'drift_unproxied'
    existing_record_snapshot TEXT,                       -- JSON
    expected_state          TEXT,                        -- JSON
    detected_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolution              TEXT,                        -- 'replaced'|'adopted'|'skipped'|'accepted_external'|'pending'
    resolved_by             TEXT,                        -- 'user:{id}'|'auto_policy'
    resolved_at             TIMESTAMP,
    dns_backup_id           INTEGER,                     -- if resolution created a backup
    route_id                INTEGER,                     -- if resolution resulted in a route
    FOREIGN KEY (dns_backup_id) REFERENCES dns_backups(id) ON DELETE SET NULL,
    FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE SET NULL
);

CREATE INDEX idx_dns_conflicts_unresolved ON dns_conflicts(resolution)
    WHERE resolution IS NULL OR resolution = 'pending';
```

### `drift_findings`

Output of periodic drift detection scans.

```sql
CREATE TABLE drift_findings (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id                 TEXT NOT NULL,              -- UUID grouping findings by scan
    finding_type            TEXT NOT NULL,
    -- 'dns_content_changed'|'dns_missing'|'dns_unproxied'|'ingress_drift'|'tunnel_missing'|'sidecar_missing'
    entity_type             TEXT NOT NULL,              -- 'route'|'tunnel'
    entity_id               INTEGER NOT NULL,
    severity                TEXT NOT NULL DEFAULT 'warning', -- 'info'|'warning'|'error'
    expected_value          TEXT,
    actual_value            TEXT,
    details_json            TEXT,
    detected_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolution              TEXT,                        -- 'reconciled_to_db'|'accepted_external'|'ignored'|'pending'
    resolved_at             TIMESTAMP,
    resolved_by             TEXT
);

CREATE INDEX idx_drift_scan ON drift_findings(scan_id);
CREATE INDEX idx_drift_unresolved ON drift_findings(resolution)
    WHERE resolution IS NULL;
CREATE INDEX idx_drift_entity ON drift_findings(entity_type, entity_id);
```

### `zone_cache`

Cached zone list from CF for fast hostname resolution without per-request API calls.

```sql
CREATE TABLE zone_cache (
    zone_id                 TEXT PRIMARY KEY,
    zone_name               TEXT NOT NULL,
    account_id              TEXT NOT NULL,
    plan_name               TEXT,                        -- 'free'|'pro'|'business'|'enterprise'
    status                  TEXT,                        -- 'active'|'pending'|'paused'
    refreshed_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_zone_cache_name ON zone_cache(zone_name);
CREATE INDEX idx_zone_cache_account ON zone_cache(account_id);
```

Refreshed on startup and every 15 minutes (or on-demand when resolution fails).

### `container_cache`

Cache of known containers to survive brief Docker event gaps and for efficient compose-project lookups.

```sql
CREATE TABLE container_cache (
    container_id            TEXT PRIMARY KEY,
    container_name          TEXT NOT NULL,
    compose_project         TEXT,
    compose_service         TEXT,
    image                   TEXT,
    status                  TEXT,                        -- 'running'|'exited'|'paused'|...
    networks_json           TEXT,                        -- JSON array of network names
    exposed_ports_json      TEXT,                        -- JSON array of port definitions
    labels_json             TEXT,
    managed_by_tm           BOOLEAN NOT NULL DEFAULT 0,
    first_seen_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_container_cache_compose ON container_cache(compose_project, compose_service);
CREATE INDEX idx_container_cache_managed ON container_cache(managed_by_tm);
```

### `app_settings`

Key-value store for app-level settings not tied to specific entities.

```sql
CREATE TABLE app_settings (
    key                     TEXT PRIMARY KEY,
    value                   TEXT NOT NULL,              -- JSON
    updated_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Known keys:
- `bootstrap.completed` — boolean, first-run flag
- `policy.dns_conflict_default` — 'ask'|'backup_replace'|'refuse'
- `policy.orphan_default` — 'ask'|'auto_adopt'|'auto_delete'
- `policy.drift_default` — 'alert'|'auto_reconcile'|'auto_accept'
- `ui.theme` — 'light'|'dark'|'system'
- `backup.litestream_enabled` — boolean
- `rotation.jitter_hours` — integer
- `rotation.stagger_minutes` — integer

### `audit_log`

Generic append-only log for all state-changing user actions.

```sql
CREATE TABLE audit_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    actor                   TEXT NOT NULL,              -- 'user:{id}'|'system'|'scheduler'
    action                  TEXT NOT NULL,              -- 'route.create'|'tunnel.delete'|'rotation.trigger'|...
    entity_type             TEXT,
    entity_id               INTEGER,
    request_id              TEXT,                        -- for correlating logs across services
    before_json             TEXT,                        -- state before change
    after_json              TEXT,                        -- state after change
    status                  TEXT NOT NULL,              -- 'success'|'failed'
    error_message           TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_time ON audit_log(created_at DESC);
CREATE INDEX idx_audit_action ON audit_log(action);
```

Default retention: indefinite. Manual purge via admin endpoint.

## Relationships

```
cf_credentials ──┐
                 ├──< tunnels ──< routes
                 │                    │
                 │                    └──< dns_operations
                 │                    └──< dns_backups
                 │                    └──< dns_conflicts
                 │
                 └── (implied, via account_id)

tunnels ──< rotation_events (entity_type='tunnel')
cf_credentials ──< rotation_events (entity_type='cf_credential')

drift_findings ──> tunnels OR routes (polymorphic via entity_type)
audit_log ──> any entity (polymorphic)
```

## Pydantic / SQLModel Conventions

- Every table has a corresponding `SQLModel` class in `app/models/`.
- Every table has input/output DTOs in `app/schemas/` (Create, Update, Read variants).
- Never return DB models directly from HTTP handlers — always map to `Read` schema.
- Encrypted columns are exposed as `SecretStr` in `Read` schemas (GUI sees only last 4 chars), plaintext in service-layer DTOs only.

Example pattern:

```python
# models/tunnel.py
class Tunnel(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    cf_tunnel_id: str = Field(unique=True, index=True)
    cf_tunnel_name: str
    token_encrypted: bytes
    token_last_four: str
    # ...

# schemas/tunnel.py
class TunnelRead(BaseModel):
    id: int
    cf_tunnel_id: str
    cf_tunnel_name: str
    token_last_four: str  # never expose encrypted blob
    status: str
    # ...

class TunnelCreate(BaseModel):
    name: str
    rotation_policy: str = 'manual'
    primary_compose_project: str | None = None
    primary_compose_service: str | None = None
```

## Migration Strategy

Alembic with the following conventions:
- All schema changes via migrations, never direct DDL.
- Migration messages describe intent, not mechanics: `add_route_path_regex` not `alter_routes_add_column`.
- Data migrations separate from schema migrations where possible.
- Every migration must have a working `downgrade()` for at least one version back.
- Initial migration creates entire schema as `0001_initial.py`.

## WAL and Concurrency

SQLite in WAL mode supports multiple concurrent readers plus one writer. Configuration:

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
```

Set at connection time in SQLAlchemy engine creation. Foreign keys must be explicitly enabled per-connection.

## Data Volume Estimates

For a VPS running 20 projects with 50 routes total:
- `tunnels`: 20 rows
- `routes`: 50 rows
- `rotation_events`: ~1200/year at quarterly rotation
- `dns_operations`: ~500/year at normal churn
- `audit_log`: ~5000/year

Total DB size well under 10MB after a year. SQLite handles this trivially.
