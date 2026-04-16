# 03 — Cloudflare Integration

## API Token Requirements

Single token with the following scopes:

| Scope | Level | Purpose |
|-------|-------|---------|
| Cloudflare Tunnel | Account, Edit | Tunnel CRUD, token fetch, ingress config |
| DNS | Zone, Edit | CNAME create/update/delete per route |
| Zone | Zone, Read | Hostname resolution, zone enumeration |
| Zone Settings | Zone, Read | Plan detection (apex CNAME support) |
| Account Settings | Account, Read | Account ID verification |

**Zone resources:** `All zones from an account` (recommended) OR specific zones only (more secure, less convenient for new zones).

**Account resources:** The single target account.

**TTL:** Optional; if set, manager tracks expiration and alerts at 30/7/1 day intervals.

**Client IP filtering:** Optional; recommend binding to VPS IP for defense in depth.

## API Endpoints Used

Base URL: `https://api.cloudflare.com/client/v4`

### Token Verification
```
GET /user/tokens/verify
```
Called on: first token entry, app startup, daily scheduled check.

Response 200 means token is valid. Extract `expires_on` if present.

### Scope Introspection
```
GET /user/tokens/permission_groups
```
Returns the permission groups attached to the token. Used to validate required scopes are present and warn about over-scoped tokens.

### Account Operations
```
GET /accounts                          # list accounts
GET /accounts/{id}                      # get account details
```

### Zone Operations
```
GET  /zones?account.id={id}&per_page=50  # list zones (paginated)
GET  /zones/{zone_id}                    # zone details (plan, status)
```

Cache result in `zone_cache` table; refresh every 15 minutes.

### Tunnel Operations
```
GET    /accounts/{id}/cfd_tunnel?is_deleted=false       # list tunnels
POST   /accounts/{id}/cfd_tunnel                         # create tunnel
GET    /accounts/{id}/cfd_tunnel/{tunnel_id}             # get tunnel
DELETE /accounts/{id}/cfd_tunnel/{tunnel_id}             # delete tunnel
GET    /accounts/{id}/cfd_tunnel/{tunnel_id}/token       # get tunnel token
GET    /accounts/{id}/cfd_tunnel/{tunnel_id}/connections # list active connections
```

### Ingress Configuration (Remotely-Managed)
```
GET /accounts/{id}/cfd_tunnel/{tunnel_id}/configurations
PUT /accounts/{id}/cfd_tunnel/{tunnel_id}/configurations
```

The `PUT` replaces the entire configuration atomically. Always include complete ingress list including catch-all.

### DNS Operations
```
GET    /zones/{zone_id}/dns_records?name={hostname}      # find existing
POST   /zones/{zone_id}/dns_records                       # create
PATCH  /zones/{zone_id}/dns_records/{record_id}          # update
DELETE /zones/{zone_id}/dns_records/{record_id}          # delete
```

## Tunnel Creation Flow

```python
async def create_tunnel(name: str, account_id: str) -> dict:
    # POST /accounts/{id}/cfd_tunnel
    # Body: {"name": "...", "config_src": "cloudflare"}
    # config_src="cloudflare" enables remotely-managed ingress
    response = await cf_client.post(
        f"/accounts/{account_id}/cfd_tunnel",
        json={"name": name, "config_src": "cloudflare"}
    )
    tunnel = response["result"]
    # tunnel["id"] is the UUID
    # tunnel["token"] is NOT in the create response; fetch separately
    return tunnel
```

Then immediately fetch the token:

```python
async def get_tunnel_token(account_id: str, tunnel_id: str) -> str:
    response = await cf_client.get(
        f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token"
    )
    return response["result"]  # a base64 string
```

## Ingress Configuration Format

```json
{
  "config": {
    "ingress": [
      {
        "hostname": "app.example.com",
        "service": "http://app-a:3000",
        "originRequest": {
          "noTLSVerify": false,
          "connectTimeout": "30s"
        }
      },
      {
        "hostname": "api.example.com",
        "path": "^/v1/.*",
        "service": "http://app-a:3000/v1"
      },
      {
        "service": "http_status:404"
      }
    ],
    "warp-routing": {
      "enabled": false
    }
  }
}
```

Rules:
- First match wins. Order matters.
- The last rule must be a catch-all (no `hostname` field).
- Service URLs are reached from cloudflared's network context, so use Docker service names.
- `originRequest` fields are optional per rule; documented options:
  - `noTLSVerify`: bool — skip upstream TLS verification
  - `httpHostHeader`: string — override Host header
  - `originServerName`: string — SNI for TLS
  - `connectTimeout`: string (`"30s"`)
  - `tcpKeepAlive`: string
  - `http2Origin`: bool
  - `disableChunkedEncoding`: bool
  - `keepAliveConnections`: int
  - `keepAliveTimeout`: string

## DNS Record Format

```json
{
  "type": "CNAME",
  "name": "app.example.com",
  "content": "abc123-def4-5678-9012-345678901234.cfargotunnel.com",
  "proxied": true,
  "ttl": 1,
  "comment": "tunnel-manager:route_id=42"
}
```

Notes:
- `ttl: 1` means "auto" (CF-managed).
- `proxied: true` is required for tunnel routing to work.
- `comment` prefix `tunnel-manager:` is the ownership signal. All DNS safety logic keys on this prefix.
- `name` can be full hostname or just the subdomain; CF normalizes to full name.

## Zone Resolution Algorithm

Given a hostname like `api.staging.app.example.com`, find the zone:

```python
async def resolve_zone(hostname: str) -> tuple[str, str]:
    """Returns (zone_id, zone_name) for the most specific matching zone."""
    
    zones = await get_cached_zones()  # refreshes from CF if stale
    zone_names_by_name = {z.zone_name: z for z in zones}
    
    parts = hostname.split('.')
    # Try longest suffix first
    for i in range(len(parts) - 1):  # -1 because TLD alone is never a zone
        candidate = '.'.join(parts[i:])
        if candidate in zone_names_by_name:
            zone = zone_names_by_name[candidate]
            if zone.status != 'active':
                raise ZoneInactiveError(f"Zone {candidate} status: {zone.status}")
            return zone.zone_id, zone.zone_name
    
    raise ZoneNotFoundError(
        f"No zone in account matches {hostname}. "
        f"Available zones: {sorted(zone_names_by_name.keys())}"
    )
```

Edge cases:
- Apex records (hostname == zone name): allowed if zone plan supports CNAME flattening (Pro+) OR if user accepts that apex won't work.
- Wildcards like `*.dev.example.com`: strip the `*.` prefix for zone matching on `dev.example.com`.
- Multi-account setups: iterate zones across all accessible accounts.

## Conflict Detection

Before creating DNS, check for existing records:

```python
async def check_dns_conflict(hostname: str, zone_id: str) -> ConflictResult:
    existing = await cf_client.get(
        f"/zones/{zone_id}/dns_records",
        params={"name": hostname, "type": "A,AAAA,CNAME"}
    )
    
    records = existing["result"]
    if not records:
        return ConflictResult(status="clear", action="proceed")
    
    for record in records:
        comment = record.get("comment") or ""
        if comment.startswith("tunnel-manager:"):
            # our record
            route_id_match = re.match(r"tunnel-manager:route_id=(\d+)", comment)
            if route_id_match:
                route_id = int(route_id_match.group(1))
                if route := db.get_route(route_id):
                    return ConflictResult(
                        status="conflict_owned_tracked",
                        existing_route=route,
                        existing_record=record
                    )
            return ConflictResult(
                status="conflict_owned_orphaned",
                existing_record=record
            )
        else:
            return ConflictResult(
                status="conflict_external",
                existing_record=record
            )
    
    return ConflictResult(status="clear")
```

## Rate Limits

Cloudflare API limit: 1200 requests per 5 minutes per token.

Mitigation strategies:
- Cache zones (15 min TTL).
- Cache account info (1 hour TTL).
- Batch operations where possible (ingress update is already atomic for multi-rule changes).
- Stagger scheduled rotations with jitter.
- On 429 response: exponential backoff (1s, 2s, 4s, 8s, max 60s), respect `Retry-After` header.

## Error Handling

CF API error responses have a consistent structure:

```json
{
  "success": false,
  "errors": [
    {"code": 1001, "message": "..."}
  ]
}
```

Common error codes worth handling specifically:

| Code | Meaning | Action |
|------|---------|--------|
| 1001 | Invalid request | Log, return 400 |
| 1003 | Zone not found | Refresh zone cache, retry once |
| 6003 | Invalid auth | Mark token invalid, alert user |
| 7003 | Zone not active | Surface to user |
| 10000 | Authentication | Token expired/revoked |
| 81057 | Record already exists | Trigger conflict resolution |
| 81058 | Content for record invalid | Log + fail the operation |

## Retry Policy

```python
@retry(
    retry=retry_if_exception_type((httpx.NetworkError, RateLimitError)),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    before_sleep=log_retry_attempt
)
async def cf_request(method, path, **kwargs):
    ...
```

Do NOT retry:
- 4xx errors other than 429 (they won't succeed on retry)
- Auth failures (6003, 10000)
- Validation errors

## Tunnel Connection Status

For health monitoring:

```python
async def get_tunnel_connections(account_id: str, tunnel_id: str) -> list[dict]:
    """Returns list of active cloudflared → edge connections."""
    response = await cf_client.get(
        f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/connections"
    )
    return response["result"]
```

Each connection has:
- `id`: unique connection ID
- `colo_name`: CF edge location (e.g., "IAD")
- `is_pending_reconnect`: bool
- `client_id`: cloudflared instance ID
- `opened_at`: timestamp

Healthy tunnel: ≥2 active connections in different colos. This is what CF's HA model expects.

Poll every 30 seconds; cache result for GUI.

## Tunnel Deletion

Prerequisites before calling DELETE:
1. Remove all DNS records referencing this tunnel
2. Stop and remove cloudflared sidecars (they must disconnect from CF edge)
3. Wait for connections to drop to zero (poll `/connections`, max 30s wait)
4. Call DELETE

If connections don't drop, CF returns an error. Force-delete is not supported via API — the tunnel must be clean.

## Account Discovery

On first-run or token change:

```python
async def discover_accounts() -> list[dict]:
    response = await cf_client.get("/accounts")
    accounts = response["result"]
    # Each account: {"id": "...", "name": "...", "type": "..."}
    return accounts
```

Store selected account in `app_settings` (`active_account_id`). Re-run discovery when token changes or user clicks "refresh."

## Ingress Rule Equivalence Check

For drift detection, compare DB-derived ingress vs CF-side config:

```python
def ingress_rules_equivalent(cf_config: dict, db_config: dict) -> bool:
    cf_rules = cf_config.get("config", {}).get("ingress", [])
    db_rules = db_config.get("ingress", [])
    
    if len(cf_rules) != len(db_rules):
        return False
    
    for cf_rule, db_rule in zip(cf_rules, db_rules):
        if normalize_rule(cf_rule) != normalize_rule(db_rule):
            return False
    
    return True

def normalize_rule(rule: dict) -> dict:
    # Remove None values, sort keys, normalize service URL
    return {
        k: v for k, v in sorted(rule.items())
        if v is not None and v != ""
    }
```

## CF SDK vs Raw httpx

Recommendation: use raw `httpx.AsyncClient` with a thin wrapper, not the official `cloudflare` Python SDK.

Reasons:
- Official SDK is sync-first (async support is newer and incomplete)
- Thin wrapper gives us control over retry/caching/rate limiting
- Fewer dependencies, smaller attack surface
- The API surface we actually use is small (~15 endpoints)

Wrapper shape:

```python
class CloudflareClient:
    def __init__(self, token: str):
        self._client = httpx.AsyncClient(
            base_url="https://api.cloudflare.com/client/v4",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0
        )
    
    async def get(self, path: str, **kwargs): ...
    async def post(self, path: str, **kwargs): ...
    async def put(self, path: str, **kwargs): ...
    async def patch(self, path: str, **kwargs): ...
    async def delete(self, path: str, **kwargs): ...
    
    # Domain-specific methods for common operations
    async def list_tunnels(self, account_id: str) -> list[dict]: ...
    async def create_tunnel(self, account_id: str, name: str) -> dict: ...
    async def update_tunnel_config(self, account_id: str, tunnel_id: str, config: dict): ...
    # etc.
```

## Security Considerations

- Never log the CF API token.
- Never log tunnel tokens.
- Never include tokens in error messages or stack traces.
- Mask sensitive fields in request/response logs: show only last 4 chars.
- Redact `Authorization` header in all log output.
- Secrets are loaded at construction, never reread from disk on each request.
- On token revocation/404: mark DB credential as invalid, stop operations, alert user.
