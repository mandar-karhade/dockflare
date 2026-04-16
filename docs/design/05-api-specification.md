# 05 — API Specification

## Conventions

- Base URL: `/api/v1`
- Content type: `application/json` for request/response bodies
- Authentication: session cookie (set by login endpoint) for UI; optional bearer token for programmatic access
- All timestamps in ISO 8601 UTC
- All IDs are integers unless otherwise noted (CF IDs are strings)
- Errors follow RFC 7807 problem+json format
- Pagination via `?page=1&per_page=50` query params
- Pagination response: `{"items": [...], "page": 1, "per_page": 50, "total": 123}`

## Error Response Format

```json
{
  "type": "https://tunnel-manager.dev/errors/dns-conflict",
  "title": "DNS record already exists",
  "status": 409,
  "detail": "An A record for app.example.com points to 1.2.3.4 and was not created by tunnel-manager.",
  "instance": "/api/v1/routes",
  "code": "dns_conflict_external",
  "resolution_options": [
    {"action": "replace_with_backup", "label": "Replace (backup original)"},
    {"action": "abort", "label": "Cancel"}
  ]
}
```

## Endpoints

### System / Health

```
GET  /api/v1/health
```
Returns overall system health. 200 with `{"status": "ok"}` when:
- DB reachable
- Docker reachable
- CF API reachable (if credential configured)

Otherwise 503 with details.

```
GET  /api/v1/version
```
Returns `{"version": "1.0.0", "build": "...", "commit": "..."}`.

```
GET  /api/v1/info
```
Returns basic info for UI bootstrap:
```json
{
  "bootstrap_completed": true,
  "active_account_id": "...",
  "active_account_name": "My Account",
  "active_credential_id": 1,
  "active_credential_status": "valid",
  "cloudflared_image": "cloudflare/cloudflared:latest",
  "scheduler_running": true
}
```

### Bootstrap / Onboarding

```
POST /api/v1/bootstrap/token
Body: {"token": "...", "name": "prod-token"}
```
Verifies token via `/user/tokens/verify`, validates scopes, persists encrypted.
Returns `{"credential_id": 1, "accounts": [...], "scopes": {...}, "warnings": [...]}`.

```
POST /api/v1/bootstrap/account
Body: {"account_id": "..."}
```
Sets the active account. Triggers initial zone/tunnel discovery scan.

```
POST /api/v1/bootstrap/scan
```
Scans the CF account for existing tunnels, DNS records, and matching Docker containers. Returns an import plan.

```
POST /api/v1/bootstrap/import
Body: {"tunnel_ids": [...], "include_orphan_dns": true}
```
Imports selected tunnels and their routes into the DB.

```
POST /api/v1/bootstrap/complete
```
Marks bootstrap as completed.

### Credentials

```
GET    /api/v1/credentials
POST   /api/v1/credentials
GET    /api/v1/credentials/{id}
PATCH  /api/v1/credentials/{id}
DELETE /api/v1/credentials/{id}
POST   /api/v1/credentials/{id}/verify       # re-verify scopes
POST   /api/v1/credentials/{id}/activate     # make it the active one
```

Response example:
```json
{
  "id": 1,
  "name": "prod-token",
  "account_id": "abc123",
  "account_name": "My Account",
  "token_last_four": "4f2a",
  "scopes": {
    "valid": true,
    "missing": [],
    "over_scoped": [],
    "details": [...]
  },
  "expires_at": "2026-10-01T00:00:00Z",
  "last_verified_at": "2026-04-16T12:00:00Z",
  "is_active": true,
  "created_at": "2026-04-01T12:00:00Z"
}
```

### Zones

```
GET  /api/v1/zones
GET  /api/v1/zones/{zone_id}
POST /api/v1/zones/refresh                    # force CF API refresh
GET  /api/v1/zones/resolve?hostname=app.foo.com   # returns matching zone
```

### Containers

```
GET  /api/v1/containers                        # all visible containers
GET  /api/v1/containers?project=myapp          # filter by compose project
GET  /api/v1/containers?managed=true           # only tunnel-manager sidecars
GET  /api/v1/containers/by-compose/{project}/{service}
GET  /api/v1/containers/{container_id}
POST /api/v1/containers/refresh                # force rescan
```

Response includes:
```json
{
  "id": "abc123...",
  "name": "myapp_web_1",
  "compose_project": "myapp",
  "compose_service": "web",
  "image": "myapp:latest",
  "status": "running",
  "networks": ["myapp_default"],
  "exposed_ports": [3000, 8080],
  "labels": {...},
  "tunnel_managed": false,
  "linked_routes": []
}
```

### Tunnels

```
GET    /api/v1/tunnels
POST   /api/v1/tunnels
GET    /api/v1/tunnels/{id}
PATCH  /api/v1/tunnels/{id}
DELETE /api/v1/tunnels/{id}
GET    /api/v1/tunnels/{id}/health            # CF connection status
POST   /api/v1/tunnels/{id}/rotate            # trigger rotation
POST   /api/v1/tunnels/{id}/force-recreate    # destructive token rotation
POST   /api/v1/tunnels/{id}/restart-sidecar
GET    /api/v1/tunnels/{id}/sidecar-logs?tail=100
GET    /api/v1/tunnels/{id}/rotation-history
```

Create body:
```json
{
  "name": "myapp-web",
  "primary_compose_project": "myapp",
  "primary_compose_service": "web",
  "rotation_policy": "30d"
}
```

Response:
```json
{
  "id": 1,
  "cf_tunnel_id": "abc123-uuid",
  "cf_tunnel_name": "myapp-web",
  "account_id": "...",
  "token_last_four": "4f2a",
  "token_fetched_at": "2026-04-16T12:00:00Z",
  "primary_compose_project": "myapp",
  "primary_compose_service": "web",
  "cloudflared_container_id": "def456...",
  "rotation_policy": "30d",
  "next_rotation_due": "2026-05-16T12:00:00Z",
  "status": "active",
  "edge_connections": 2,
  "routes_count": 3,
  "created_at": "2026-04-16T12:00:00Z"
}
```

### Routes

```
GET    /api/v1/routes                         # all routes
GET    /api/v1/routes?tunnel_id=1
GET    /api/v1/routes?project=myapp
POST   /api/v1/routes
GET    /api/v1/routes/{id}
PATCH  /api/v1/routes/{id}
DELETE /api/v1/routes/{id}
POST   /api/v1/routes/{id}/enable
POST   /api/v1/routes/{id}/disable
POST   /api/v1/routes/{id}/check-conflict     # pre-flight DNS conflict check
POST   /api/v1/routes/reorder                 # bulk priority update
  Body: [{"id": 1, "priority": 100}, ...]
POST   /api/v1/routes/test-match              # test which rule matches hostname+path
  Body: {"tunnel_id": 1, "hostname": "app.foo.com", "path": "/api/x"}
```

Create body:
```json
{
  "tunnel_id": 1,
  "hostname": "app.example.com",
  "path_regex": null,
  "target_compose_project": "myapp",
  "target_compose_service": "web",
  "target_scheme": "http",
  "target_port": 3000,
  "target_path_prefix": null,
  "priority": null,
  "origin_options": {
    "no_tls_verify": false,
    "http_host_header": null,
    "connect_timeout_seconds": 30,
    "http2_origin": false
  },
  "conflict_resolution": null
  // Values: null (ask), "replace_with_backup", "replace_no_backup", "abort"
}
```

If there's a conflict and `conflict_resolution` is null, responds 409 with details. Client then re-POSTs with explicit resolution.

Response:
```json
{
  "id": 42,
  "tunnel_id": 1,
  "hostname": "app.example.com",
  "path_regex": null,
  "priority": 700,
  "target_compose_project": "myapp",
  "target_compose_service": "web",
  "target_scheme": "http",
  "target_port": 3000,
  "zone_id": "...",
  "zone_name": "example.com",
  "cf_dns_record_id": "...",
  "dns_proxied": true,
  "enabled": true,
  "status": "active",
  "last_healthy_at": "2026-04-16T12:00:00Z",
  "public_url": "https://app.example.com",
  "origin_options": {...},
  "created_at": "2026-04-16T12:00:00Z"
}
```

### DNS Operations

```
GET  /api/v1/dns/records                      # all tunnel-manager records across zones
GET  /api/v1/dns/records?zone_id=...
GET  /api/v1/dns/operations                   # audit log
GET  /api/v1/dns/backups                      # backed-up replaced records
POST /api/v1/dns/backups/{id}/restore         # restore a backup
DELETE /api/v1/dns/backups/{id}               # delete backup
```

### Reconciliation / Drift

```
POST /api/v1/reconcile/scan                   # trigger drift scan now
GET  /api/v1/reconcile/findings               # all unresolved findings
GET  /api/v1/reconcile/findings?scan_id=...
POST /api/v1/reconcile/findings/{id}/resolve
  Body: {"action": "reconcile_to_db" | "accept_external" | "ignore"}
POST /api/v1/reconcile/orphans/scan           # CF-side orphan records
GET  /api/v1/reconcile/orphans
POST /api/v1/reconcile/orphans/adopt          # bulk adopt orphan records
  Body: {"record_ids": [...]}
POST /api/v1/reconcile/orphans/delete         # bulk delete orphan records
  Body: {"record_ids": [...]}
```

### Rotation

```
GET  /api/v1/rotation/events                  # history across all entities
GET  /api/v1/rotation/events?entity_type=tunnel&entity_id=1
POST /api/v1/rotation/bulk                    # rotate all due
  Body: {"dry_run": false, "filter": {"project": "myapp"}}
```

### Audit Log

```
GET  /api/v1/audit?limit=100
GET  /api/v1/audit?actor=user:1
GET  /api/v1/audit?action=route.create
GET  /api/v1/audit?entity_type=tunnel&entity_id=1
```

### Settings

```
GET   /api/v1/settings
PATCH /api/v1/settings
  Body: {"policy.dns_conflict_default": "backup_replace", ...}
```

### WebSocket Endpoints

```
/api/v1/ws/events
```

Bidirectional WebSocket for live updates. Server pushes:

```json
{"type": "container.changed", "data": {"id": "...", "status": "running"}}
{"type": "tunnel.changed", "data": {...}}
{"type": "route.changed", "data": {...}}
{"type": "tunnel.health", "data": {"tunnel_id": 1, "connections": 2}}
{"type": "drift.detected", "data": {"finding_id": 1, ...}}
{"type": "rotation.progress", "data": {"event_id": 1, "step": "restart_sidecar"}}
{"type": "notification", "data": {"level": "info", "message": "..."}}
```

Client can send:
```json
{"type": "subscribe", "channels": ["tunnels", "routes", "health"]}
{"type": "ping"}
```

## Validation Rules

### Hostname
- Must be a valid DNS name
- Max 253 chars total, each label max 63 chars
- Wildcard allowed as leftmost label only: `*.dev.example.com` ✓, `dev.*.example.com` ✗
- Must match an accessible zone in the active account

### Path regex
- Valid Go `regexp` syntax (what cloudflared supports)
- Max 500 chars
- Rejected if matches everything (`.*` alone is usually an ingress mistake; warn)

### Target port
- 1-65535
- Not required for `target_scheme=unix`

### Rotation policy
- `manual` or `7d`/`30d`/`90d` or cron expression (future)

### Target compose project/service
- Alphanumeric + `-`/`_` per Docker naming rules
- Either both or neither provided (if neither, `target_container_name` required)

## Rate Limiting

- General API: 100 req/min per client (in-memory sliding window)
- Write endpoints: 20 req/min
- Bootstrap endpoints: 10 req/min
- WebSocket: 1 connection per session, unlimited messages
- Returns 429 with `Retry-After` on exceed

## OpenAPI

FastAPI auto-generates OpenAPI at `/api/v1/openapi.json`. Swagger UI at `/api/v1/docs`. ReDoc at `/api/v1/redoc`.

## Auth Model

### Session-based (UI)
- `POST /api/v1/auth/login` with credentials → sets HTTP-only secure cookie
- Cookie carries an opaque session ID, resolved to user in backend
- Single-user mode for v1 (admin account only); multi-user later

### Bearer token (programmatic)
- Admin can mint API tokens via `POST /api/v1/auth/tokens`
- Tokens stored hashed in DB
- Header: `Authorization: Bearer tm_...`
- Scope: either full admin or read-only (v1 simple model)

### No auth required
- `/api/v1/health` (for external monitoring)
- `/api/v1/version`

## CORS

Default: disabled (served same-origin as UI). Configurable via `app_settings.cors.allowed_origins` for split deployment.

## Request Correlation

Every request assigned a `X-Request-ID` (generated if client doesn't provide). Included in logs and audit entries. Returned in response header.
