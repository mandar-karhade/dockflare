# 10 — Deployment

## Prerequisites

- Linux VPS with Docker Engine 24+ and Docker Compose v2
- Public IPv4 address (needed only for outbound; no inbound ports required beyond SSH)
- A Cloudflare account with at least one zone
- A Cloudflare API token with required scopes (see [03-cloudflare-integration.md](03-cloudflare-integration.md))
- Optional: R2 (or S3-compatible) bucket for Litestream backups

## Directory Layout on Host

```
/opt/tunnel-manager/
├── docker-compose.yml
├── .env                        # non-secret config only
├── secrets/
│   ├── master_key              # chmod 400
│   ├── litestream_access_key   # chmod 400
│   └── litestream_secret_key   # chmod 400
├── data/
│   └── tm.db                   # SQLite, owned by manager UID
├── litestream/
│   └── litestream.yml
└── backups/                    # local cold backups (optional)
```

## First-Time Setup

```bash
sudo mkdir -p /opt/tunnel-manager/{secrets,data,litestream,backups}
cd /opt/tunnel-manager

# Generate master encryption key (32 random bytes, hex-encoded)
openssl rand -hex 32 > secrets/master_key
chmod 400 secrets/master_key

# BACK UP THIS FILE SOMEWHERE SAFE. Without it, the DB is unreadable.
# Suggested: print and store in password manager, plus encrypted copy elsewhere.

# If using Litestream, provision R2 bucket and token, then:
echo "your-r2-access-key-id" > secrets/litestream_access_key
echo "your-r2-secret-access-key" > secrets/litestream_secret_key
chmod 400 secrets/litestream_*
```

## Compose File

`/opt/tunnel-manager/docker-compose.yml`:

```yaml
services:
  socket-proxy:
    image: tecnativa/docker-socket-proxy:0.3.0
    container_name: tm-socket-proxy
    restart: unless-stopped
    environment:
      CONTAINERS: 1
      NETWORKS: 1
      IMAGES: 1
      EVENTS: 1
      VERSION: 1
      INFO: 1
      PING: 1
      POST: 1
      AUTH: 0
      BUILD: 0
      COMMIT: 0
      CONFIGS: 0
      DISTRIBUTION: 0
      EXEC: 0
      NODES: 0
      PLUGINS: 0
      SECRETS: 0
      SERVICES: 0
      SESSION: 0
      SWARM: 0
      SYSTEM: 0
      TASKS: 0
      VOLUMES: 0
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - tm-internal
    read_only: true
    tmpfs:
      - /run
    cap_drop: [ALL]
    cap_add: [CHOWN, SETUID, SETGID]
    security_opt:
      - no-new-privileges:true

  manager:
    image: tunnel-manager:${TM_VERSION:-latest}
    container_name: tm-manager
    restart: unless-stopped
    depends_on:
      - socket-proxy
    environment:
      DOCKER_HOST: tcp://socket-proxy:2375
      DATABASE_URL: sqlite+aiosqlite:////data/tm.db
      MASTER_KEY_FILE: /run/secrets/master_key
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      TZ: ${TZ:-UTC}
      # Optional:
      # TM_BIND_HOST: 127.0.0.1
      # TM_WEBHOOK_URL: https://hooks.slack.com/...
    volumes:
      - ./data:/data
    secrets:
      - master_key
    networks:
      - tm-internal
    ports:
      # Bind to localhost; expose via CF tunnel with Access for auth
      - "127.0.0.1:8088:8088"
    read_only: true
    tmpfs:
      - /tmp:size=32m
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8088/api/v1/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s

  litestream:
    image: litestream/litestream:0.3
    container_name: tm-litestream
    restart: unless-stopped
    depends_on:
      - manager
    command: replicate
    environment:
      LITESTREAM_ACCESS_KEY_ID_FILE: /run/secrets/litestream_access_key
      LITESTREAM_SECRET_ACCESS_KEY_FILE: /run/secrets/litestream_secret_key
    volumes:
      - ./data:/data:ro
      - ./litestream/litestream.yml:/etc/litestream.yml:ro
    secrets:
      - litestream_access_key
      - litestream_secret_key
    read_only: true
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    profiles: [with-backup]

secrets:
  master_key:
    file: ./secrets/master_key
  litestream_access_key:
    file: ./secrets/litestream_access_key
  litestream_secret_key:
    file: ./secrets/litestream_secret_key

networks:
  tm-internal:
    driver: bridge
    internal: false  # manager needs outbound to api.cloudflare.com
```

## Environment File

`/opt/tunnel-manager/.env`:

```ini
TM_VERSION=1.0.0
LOG_LEVEL=INFO
TZ=America/New_York
```

No secrets here. All sensitive values go through Docker secrets.

## Litestream Config

`/opt/tunnel-manager/litestream/litestream.yml`:

```yaml
dbs:
  - path: /data/tm.db
    replicas:
      - type: s3
        bucket: tunnel-manager-backups
        path: production
        endpoint: https://<account-id>.r2.cloudflarestorage.com
        region: auto
        retention: 720h        # 30 days
        snapshot-interval: 24h
        validation-interval: 168h  # weekly integrity check
```

## First Run

```bash
cd /opt/tunnel-manager

# Pull images
docker compose pull

# Start core services (no backup yet)
docker compose up -d

# Watch logs
docker compose logs -f manager
```

The manager will:
1. Load master key from secret
2. Run Alembic migrations to initialize the DB
3. Start in bootstrap mode (no CF token yet)
4. Serve GUI on 127.0.0.1:8088

## Accessing the GUI

The manager binds to localhost only. Expose it via its own CF tunnel with CF Access for authentication:

```bash
# SSH tunnel for initial setup
ssh -L 8088:localhost:8088 your-vps
# Then browse http://localhost:8088
```

After bootstrap, create a tunnel for the manager itself via the GUI and point `manager.yourdomain.com` at `localhost:8088`. Add a CF Access policy requiring login.

## Bootstrap Flow (First-Run Wizard)

1. Open GUI → redirected to `/bootstrap`
2. Paste CF API token → manager verifies via `/user/tokens/verify` and introspects scopes
3. Select active account (if token has access to multiple)
4. Scan for existing tunnels → manager calls CF list endpoints
5. Choose: import existing, or start fresh
6. For imports: per-tunnel toggle for "take over sidecar" (replaces user's compose-managed cloudflared)
7. Set policy defaults
8. Complete → redirected to dashboard

## Enabling Backups

Once stable, enable Litestream:

```bash
docker compose --profile with-backup up -d
docker compose logs litestream | head -50   # verify first snapshot
```

Verify backup restorability periodically:

```bash
docker run --rm \
  -e LITESTREAM_ACCESS_KEY_ID_FILE=/tmp/ak \
  -e LITESTREAM_SECRET_ACCESS_KEY_FILE=/tmp/sk \
  -v $(pwd)/secrets/litestream_access_key:/tmp/ak:ro \
  -v $(pwd)/secrets/litestream_secret_key:/tmp/sk:ro \
  -v $(pwd)/litestream/litestream.yml:/etc/litestream.yml:ro \
  -v /tmp:/restore \
  litestream/litestream:0.3 \
  restore -o /restore/tm-test.db /data/tm.db
```

## Upgrades

```bash
cd /opt/tunnel-manager

# Pin new version in .env
sed -i 's/TM_VERSION=.*/TM_VERSION=1.1.0/' .env

# Pull and recreate manager (sidecars remain untouched)
docker compose pull manager
docker compose up -d manager

# Migrations run automatically on startup. Watch for errors.
docker compose logs -f manager
```

Tunnel sidecars are not recreated during manager upgrades — they keep serving traffic. If the new manager version requires sidecar changes, it will issue rolling restarts post-migration.

## Rollback

If an upgrade breaks:

```bash
# Stop manager
docker compose stop manager

# Restore DB from Litestream (or local backup)
cp /opt/tunnel-manager/backups/tm-pre-upgrade.db /opt/tunnel-manager/data/tm.db

# Pin previous version
sed -i 's/TM_VERSION=.*/TM_VERSION=1.0.0/' .env

# Start
docker compose up -d manager
```

Note: Alembic downgrade is best-effort. DB backups are the more reliable rollback path.

## Backup Strategy

**Tier 1 (continuous):** Litestream → R2. RPO: seconds. Recovery: restore DB, start manager.

**Tier 2 (nightly):** Cron job that takes a local `sqlite3 .backup` snapshot and compresses:

```bash
# /etc/cron.d/tunnel-manager-backup
0 3 * * * root /opt/tunnel-manager/scripts/nightly-backup.sh >> /var/log/tm-backup.log 2>&1
```

```bash
#!/usr/bin/env bash
# nightly-backup.sh
set -euo pipefail
DATE=$(date +%Y%m%d)
docker exec tm-manager sqlite3 /data/tm.db ".backup /tmp/tm-${DATE}.db"
docker cp tm-manager:/tmp/tm-${DATE}.db /opt/tunnel-manager/backups/
docker exec tm-manager rm /tmp/tm-${DATE}.db
gzip /opt/tunnel-manager/backups/tm-${DATE}.db
# Keep last 30 days
find /opt/tunnel-manager/backups -name "tm-*.db.gz" -mtime +30 -delete
```

**Tier 3 (offline):** Monthly, copy the encrypted backup and master_key to a different physical location.

## Disaster Recovery: New VPS

On fresh VPS:

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh

# 2. Create directory structure
sudo mkdir -p /opt/tunnel-manager/{secrets,data,litestream,backups}
cd /opt/tunnel-manager

# 3. Restore master key from offline backup
echo "<your-saved-master-key-hex>" > secrets/master_key
chmod 400 secrets/master_key

# 4. Restore DB from Litestream
docker run --rm \
  -e LITESTREAM_ACCESS_KEY_ID=... \
  -e LITESTREAM_SECRET_ACCESS_KEY=... \
  -v $(pwd)/litestream/litestream.yml:/etc/litestream.yml \
  -v $(pwd)/data:/data \
  litestream/litestream:0.3 \
  restore -o /data/tm.db /data/tm.db

# 5. Copy compose files
# (from version control or previous VPS)

# 6. Start services
docker compose up -d
```

All tunnels come back online once sidecars spawn. DNS records don't change because they point at `<tunnel-id>.cfargotunnel.com`, not a VPS IP.

Time-to-recovery: ~15 minutes including DNS propagation (which is usually already correct).

## Per-Project Compose Changes

After migrating a project to tunnel-manager:

**Before:**
```yaml
# project/compose.yml
services:
  app:
    image: myapp:latest
    # ...
  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run --token ${TUNNEL_TOKEN}
    restart: unless-stopped
```

**After:**
```yaml
# project/compose.yml
services:
  app:
    image: myapp:latest
    # Optional: explicit label for tunnel-manager to match by
    labels:
      tunnel-manager.eligible: "true"
    # ...
  # (no cloudflared — spawned by tunnel-manager)
```

Remove `TUNNEL_TOKEN` from the project's `.env`.

## Network Troubleshooting

The manager needs to reach:
- `api.cloudflare.com` (HTTPS/443)
- R2 endpoint (HTTPS/443, if backups enabled)

Sidecars need to reach:
- `*.cloudflare.com` (QUIC/7844 preferred, HTTP2/443 fallback)
- Their target service on the shared Docker network

If sidecars fail to connect, check:
1. Outbound UDP/7844 not blocked by host firewall
2. `docker network inspect <network>` shows both sidecar and target
3. Target container actually listening on the expected port
4. cloudflared logs (`docker logs cftunnel-<project>-<service>`)

## Resource Sizing

Per-container estimates at moderate load:

| Component | Memory | CPU |
|-----------|--------|-----|
| socket-proxy | 5-10 MB | negligible |
| manager | 80-150 MB | <1% idle, brief spikes during scans |
| litestream | 20-30 MB | negligible |
| each cloudflared sidecar | 25-50 MB | <1% idle, scales with traffic |

For a VPS running 20 projects with tunnel-manager: total ~1 GB RAM overhead from tunnel infrastructure, comparable to what it was before (just now centrally managed).

## Monitoring Integration

Manager exposes `/api/v1/health` for external probes. Return codes:
- 200: all subsystems healthy
- 503: one or more subsystems degraded; response body lists which

Optional: Prometheus metrics at `/api/v1/metrics` (behind auth or localhost-only). Key metrics:
- `tm_tunnels_total{status=...}`
- `tm_routes_total{status=...}`
- `tm_rotation_events_total{status=...}`
- `tm_drift_findings_open{severity=...}`
- `tm_cf_api_requests_total{method=, status=}`
- `tm_cf_api_request_duration_seconds`
