# 06 — Rotation Engine

## Scope

Handles rotation of three token types:

1. **Tunnel tokens** — the per-tunnel `TUNNEL_TOKEN` used by cloudflared
2. **CF API credentials** — the account-level token the manager holds
3. **Master encryption key** — the key that encrypts all other secrets

Each has a distinct workflow but a common audit model via `rotation_events`.

## Tunnel Token Rotation

### Rotation Types

| Type | Trigger | Effect | Downtime |
|------|---------|--------|----------|
| Soft rotation | Manual or scheduled | Re-fetch token from CF, redeploy sidecar | <1s (rolling) |
| Force recreation | Manual "force recreate" | Delete tunnel, create new, migrate DNS+ingress | 30-60s |
| Recovery rotation | Drift/corruption detected | Re-fetch token, restart sidecar | 1-3s |

CF's `/token` endpoint typically returns the same token for a given tunnel, so soft rotation is often a "noop" — the token hasn't changed server-side. The manager detects this and logs it as a successful noop. Force recreation is the escape hatch for genuine compromise response.

### Soft Rotation Algorithm

```python
async def rotate_tunnel_soft(tunnel_id: int, triggered_by: str) -> RotationEvent:
    tunnel = db.get_tunnel(tunnel_id)
    if not tunnel:
        raise NotFoundError(f"Tunnel {tunnel_id}")
    
    event = db.create_rotation_event(
        entity_type="tunnel",
        entity_id=tunnel_id,
        triggered_by=triggered_by,
        status="in_progress",
        started_at=datetime.utcnow(),
        old_token_last_four=tunnel.token_last_four
    )
    
    try:
        # 1. Fetch fresh token
        new_token = await cf.get_tunnel_token(tunnel.account_id, tunnel.cf_tunnel_id)
        old_token = vault.decrypt(tunnel.token_encrypted)
        
        # 2. Check if changed
        if new_token == old_token:
            db.update_rotation_event(event.id,
                status="noop",
                completed_at=datetime.utcnow(),
                error_message="Token unchanged server-side"
            )
            # Still update fetched_at to reset rotation schedule
            db.update_tunnel(tunnel_id,
                token_fetched_at=datetime.utcnow(),
                next_rotation_due=calculate_next_due(tunnel.rotation_policy)
            )
            return event
        
        # 3. Persist new token BEFORE deployment
        new_encrypted = vault.encrypt(new_token)
        db.update_tunnel(tunnel_id,
            token_encrypted=new_encrypted,
            token_last_four=new_token[-4:],
            token_fetched_at=datetime.utcnow()
        )
        
        # 4. Rolling restart of cloudflared sidecar
        rotation_start = time.monotonic()
        new_sidecar_id = await rolling_restart_sidecar(tunnel_id, new_token)
        
        # 5. Verify health post-rotation
        healthy = await wait_for_tunnel_healthy(
            tunnel.cf_tunnel_id,
            min_connections=1,
            timeout_seconds=30
        )
        if not healthy:
            raise RotationError("New sidecar did not reach healthy state")
        
        # 6. Update rotation success
        downtime = time.monotonic() - rotation_start
        db.update_tunnel(tunnel_id,
            cloudflared_container_id=new_sidecar_id,
            token_deployed_at=datetime.utcnow(),
            last_rotation_at=datetime.utcnow(),
            last_rotation_status="success",
            next_rotation_due=calculate_next_due(tunnel.rotation_policy)
        )
        db.update_rotation_event(event.id,
            status="success",
            completed_at=datetime.utcnow(),
            new_token_last_four=new_token[-4:],
            downtime_seconds=downtime
        )
        
        return event
    
    except Exception as e:
        db.update_rotation_event(event.id,
            status="failed",
            completed_at=datetime.utcnow(),
            error_message=str(e)
        )
        db.update_tunnel(tunnel_id,
            last_rotation_status=f"failed:{type(e).__name__}"
        )
        raise
```

### Rolling Restart Implementation

The "overlap" technique: run two cloudflared instances with the same token briefly, then stop the old one.

```python
async def rolling_restart_sidecar(tunnel_id: int, new_token: str) -> str:
    """Returns new container ID."""
    tunnel = db.get_tunnel(tunnel_id)
    old = docker.containers.get(tunnel.cloudflared_container_id)
    
    # Capture old container's networks before stopping it
    old_networks = get_container_networks(old)
    old_labels = dict(old.labels)
    old_name = old.name
    
    # 1. Spawn new cloudflared alongside old
    new_name = f"{old_name}-rotating-{int(time.time())}"
    new = docker.containers.run(
        image=old.image.tags[0] if old.image.tags else "cloudflare/cloudflared:latest",
        name=new_name,
        command=["tunnel", "--no-autoupdate", "run", "--token", new_token],
        network=old_networks[0],
        labels={
            **old_labels,
            "tunnel-manager.rotation.active": "true",
            "tunnel-manager.rotation.replaces": old.id
        },
        restart_policy={"Name": "unless-stopped"},
        detach=True,
        mem_limit="128m",
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"]
    )
    
    # Attach to remaining networks
    for net in old_networks[1:]:
        docker.networks.get(net).connect(new)
    
    # 2. Wait for new to establish CF connections
    #    During this window, BOTH are connected; CF load-balances
    await wait_for_cf_connections(
        tunnel.cf_tunnel_id,
        min_connections_from_client=new.id[:12],
        timeout=20
    )
    
    # 3. Gracefully stop old — its connections drain
    try:
        old.kill(signal="SIGTERM")
        old.wait(timeout=15)
    except Exception as e:
        log.warning(f"Graceful stop failed: {e}")
        old.kill()  # force
    old.remove(force=True)
    
    # 4. Rename new to canonical name
    new.rename(old_name)
    
    # 5. Remove rotation label
    # (labels can't be changed on running containers; this is noted in DB instead)
    
    return new.id
```

Critical detail: step 2 uses `wait_for_cf_connections` which polls CF's `/connections` API filtering by `client_id` to verify the new cloudflared is actively connected before stopping the old one.

### Connection Tracking

```python
async def wait_for_cf_connections(
    tunnel_id: str,
    min_connections: int = 1,
    min_connections_from_client: str | None = None,
    timeout: float = 20.0
) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        connections = await cf.get_tunnel_connections(account_id, tunnel_id)
        
        if min_connections_from_client:
            # Verify new client is represented
            client_conns = [c for c in connections
                            if c.get("client_id", "").startswith(min_connections_from_client)]
            if len(client_conns) >= 1:
                return True
        elif len(connections) >= min_connections:
            return True
        
        await asyncio.sleep(1.0)
    
    return False
```

### Force Recreation Algorithm

Used when the user demands genuinely new credentials:

```python
async def rotate_tunnel_force_recreate(tunnel_id: int, triggered_by: str):
    tunnel = db.get_tunnel(tunnel_id)
    routes = db.get_routes_for_tunnel(tunnel_id)
    
    event = db.create_rotation_event(
        entity_type="tunnel",
        entity_id=tunnel_id,
        triggered_by=f"{triggered_by}:force_recreate",
        status="in_progress",
        started_at=datetime.utcnow()
    )
    
    old_cf_tunnel_id = tunnel.cf_tunnel_id
    temp_name = f"{tunnel.cf_tunnel_name}-rotating-{int(time.time())}"
    
    try:
        # 1. Create new CF tunnel with temp name
        new_cf_tunnel = await cf.create_tunnel(tunnel.account_id, temp_name)
        new_token = await cf.get_tunnel_token(tunnel.account_id, new_cf_tunnel["id"])
        
        # 2. Copy ingress config from old to new
        old_config = await cf.get_tunnel_config(tunnel.account_id, old_cf_tunnel_id)
        await cf.update_tunnel_config(
            tunnel.account_id, new_cf_tunnel["id"], old_config["config"]
        )
        
        # 3. Spawn new cloudflared for new tunnel (alongside old)
        new_sidecar = await spawn_cloudflared_for_tunnel(
            tunnel_id=tunnel_id,
            tunnel_cf_id=new_cf_tunnel["id"],
            token=new_token,
            name_suffix="rotating"
        )
        await wait_for_cf_connections(new_cf_tunnel["id"], min_connections=1, timeout=30)
        
        # 4. Update all DNS records to point at new tunnel
        for route in routes:
            new_content = f"{new_cf_tunnel['id']}.cfargotunnel.com"
            await cf.update_dns_record(
                zone_id=route.zone_id,
                record_id=route.cf_dns_record_id,
                content=new_content
            )
        
        # 5. Wait for DNS propagation at CF edge (usually <10s)
        await asyncio.sleep(15)
        
        # 6. Stop old cloudflared, old tunnel now idle
        old_sidecar = docker.containers.get(tunnel.cloudflared_container_id)
        old_sidecar.kill(signal="SIGTERM")
        old_sidecar.wait(timeout=15)
        old_sidecar.remove(force=True)
        
        # 7. Wait for old tunnel's connections to drop to 0
        await wait_for_cf_connections_zero(old_cf_tunnel_id, timeout=30)
        
        # 8. Delete old CF tunnel
        await cf.delete_tunnel(tunnel.account_id, old_cf_tunnel_id)
        
        # 9. Rename new tunnel to canonical name
        await cf.update_tunnel(
            tunnel.account_id, new_cf_tunnel["id"],
            name=tunnel.cf_tunnel_name
        )
        
        # 10. Update DB
        new_encrypted = vault.encrypt(new_token)
        db.update_tunnel(tunnel_id,
            cf_tunnel_id=new_cf_tunnel["id"],
            token_encrypted=new_encrypted,
            token_last_four=new_token[-4:],
            token_fetched_at=datetime.utcnow(),
            token_deployed_at=datetime.utcnow(),
            cloudflared_container_id=new_sidecar.id,
            last_rotation_at=datetime.utcnow(),
            last_rotation_status="success_force_recreate"
        )
        
        db.update_rotation_event(event.id,
            status="success",
            completed_at=datetime.utcnow(),
            details_json={"old_cf_tunnel_id": old_cf_tunnel_id,
                          "new_cf_tunnel_id": new_cf_tunnel["id"]}
        )
    
    except Exception as e:
        # Best-effort cleanup
        await attempt_force_recreate_rollback(tunnel_id, event.id, e)
        raise
```

## Scheduler

APScheduler with async executor, started in FastAPI lifespan hook.

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

scheduler = AsyncIOScheduler()

def register_jobs():
    scheduler.add_job(
        rotation_scheduler_tick,
        IntervalTrigger(minutes=15),
        id="rotation_tick",
        replace_existing=True,
        max_instances=1
    )
    scheduler.add_job(
        drift_detection_scan,
        IntervalTrigger(hours=1),
        id="drift_scan",
        replace_existing=True,
        max_instances=1
    )
    scheduler.add_job(
        health_poll,
        IntervalTrigger(seconds=30),
        id="health_poll",
        replace_existing=True,
        max_instances=1
    )
    scheduler.add_job(
        backup_retention_cleanup,
        IntervalTrigger(hours=24),
        id="backup_cleanup",
        replace_existing=True
    )
    scheduler.add_job(
        verify_cf_token,
        IntervalTrigger(hours=24),
        id="token_verify",
        replace_existing=True
    )

async def rotation_scheduler_tick():
    """Runs every 15 minutes; rotates any due tunnels."""
    due = db.query_tunnels_rotation_due()
    
    # Stagger to avoid hammering CF API
    jitter_range = app_settings.get("rotation.stagger_minutes", 2)
    
    for i, tunnel in enumerate(due):
        delay = random.uniform(0, jitter_range * 60)
        asyncio.create_task(
            delayed_rotation(tunnel.id, delay, triggered_by="scheduled")
        )

async def delayed_rotation(tunnel_id: int, delay: float, triggered_by: str):
    await asyncio.sleep(delay)
    try:
        await rotate_tunnel_soft(tunnel_id, triggered_by)
    except Exception as e:
        log.error(f"Scheduled rotation failed for tunnel {tunnel_id}: {e}")
        await notify_rotation_failure(tunnel_id, e)
```

## Rotation Policy Parsing

```python
def calculate_next_due(policy: str) -> datetime | None:
    now = datetime.utcnow()
    if policy == "manual":
        return None
    if policy == "7d":
        return now + timedelta(days=7)
    if policy == "30d":
        return now + timedelta(days=30)
    if policy == "90d":
        return now + timedelta(days=90)
    # Future: cron expressions
    # from croniter import croniter
    # return croniter(policy, now).get_next(datetime)
    raise ValueError(f"Unknown policy: {policy}")
```

## CF API Credential Rotation

Simpler flow because there's only one credential at a time:

```python
async def rotate_cf_credential(new_token: str, name: str | None = None):
    # 1. Verify new token works
    temp_client = CFClient(new_token)
    verify_result = await temp_client.verify_token()
    if not verify_result["valid"]:
        raise ValidationError("New token is invalid")
    
    # 2. Check scopes match or exceed old token
    scopes = await temp_client.get_scopes()
    if not scopes["has_required"]:
        raise ValidationError(f"Missing scopes: {scopes['missing']}")
    
    # 3. Verify access to active account
    accounts = await temp_client.list_accounts()
    active_account_id = app_settings.get("active_account_id")
    if not any(a["id"] == active_account_id for a in accounts):
        raise ValidationError(
            f"New token lacks access to active account {active_account_id}"
        )
    
    # 4. Store new credential
    new_cred = await credential_service.create(
        name=name or f"rotated-{datetime.utcnow():%Y%m%d}",
        token=new_token,
        account_id=active_account_id
    )
    
    # 5. Atomic swap: deactivate old, activate new
    async with db.transaction():
        old_cred = db.get_active_credential()
        db.deactivate_credential(old_cred.id)
        db.activate_credential(new_cred.id)
    
    # 6. Reload CF client everywhere (app-level singleton)
    cf_client_manager.reload()
    
    # 7. Keep old for 7 days then delete (enables rollback)
    schedule_deletion(old_cred.id, after=timedelta(days=7))
    
    # 8. Audit event
    db.create_rotation_event(
        entity_type="cf_credential",
        entity_id=new_cred.id,
        triggered_by="manual",
        status="success",
        old_token_last_four=old_cred.token_last_four,
        new_token_last_four=new_cred.token_last_four
    )
```

## Master Key Rotation

The key that encrypts other keys. Requires re-encrypting all encrypted columns:

```python
async def rotate_master_key(new_key: bytes) -> RotationEvent:
    if len(new_key) != 32:
        raise ValueError("Master key must be 32 bytes")
    
    event = db.create_rotation_event(
        entity_type="master_key",
        entity_id=0,
        triggered_by="manual",
        status="in_progress"
    )
    
    try:
        old_vault = VaultService(key=current_master_key)
        new_vault = VaultService(key=new_key)
        
        async with db.transaction():
            # Re-encrypt tunnel tokens
            for tunnel in db.query_all_tunnels():
                plaintext = old_vault.decrypt(tunnel.token_encrypted)
                new_encrypted = new_vault.encrypt(plaintext)
                db.update_tunnel_token_encryption(tunnel.id, new_encrypted)
            
            # Re-encrypt CF credentials
            for cred in db.query_all_credentials():
                plaintext = old_vault.decrypt(cred.token_encrypted)
                new_encrypted = new_vault.encrypt(plaintext)
                db.update_credential_token_encryption(cred.id, new_encrypted)
        
        # Swap active key
        write_new_master_key(new_key)
        archive_old_master_key(current_master_key, retention_days=7)
        reload_vault_service(new_key)
        
        db.update_rotation_event(event.id, status="success", completed_at=datetime.utcnow())
    
    except Exception as e:
        # DB transaction rolled back; safe to retry
        db.update_rotation_event(event.id, status="failed", error_message=str(e))
        raise
```

## Notification Hooks

On rotation failure, notify via:

1. **Database record** (always) — surfaces in GUI
2. **WebSocket push** — live alert if GUI is open
3. **Webhook** (optional) — Slack/Discord/generic URL from settings
4. **Email** (future) — if SMTP configured

Webhook payload:

```json
{
  "event": "rotation.failed",
  "tunnel_id": 1,
  "tunnel_name": "myapp-web",
  "error": "...",
  "timestamp": "2026-04-16T12:00:00Z",
  "dashboard_url": "https://tm.example.com/tunnels/1"
}
```

## Rotation History UI Surface

Per-tunnel view shows:
- Current token age
- Next rotation due
- Last 5 rotation events with status + downtime
- Button: Rotate Now (soft)
- Button: Force Recreate (destructive, requires confirmation)

Global dashboard widget shows rollup counts:
- Due for rotation
- Rotated in last 30 days
- Failed rotations

## Bulk Operations

```python
async def bulk_rotate(
    filter_project: str | None = None,
    filter_status: str | None = None,
    dry_run: bool = False
) -> dict:
    tunnels = db.query_tunnels(
        primary_compose_project=filter_project,
        status=filter_status
    )
    
    results = []
    for tunnel in tunnels:
        if dry_run:
            results.append({"tunnel_id": tunnel.id, "action": "would_rotate"})
            continue
        
        try:
            event = await rotate_tunnel_soft(tunnel.id, triggered_by="bulk")
            results.append({
                "tunnel_id": tunnel.id,
                "status": event.status,
                "downtime_seconds": event.downtime_seconds
            })
        except Exception as e:
            results.append({"tunnel_id": tunnel.id, "status": "failed", "error": str(e)})
        
        # Rate limit protection
        await asyncio.sleep(2)
    
    return {"results": results, "total": len(tunnels)}
```

## Edge Cases

### Sidecar missing at rotation time
If the tracked sidecar container doesn't exist (crashed without restart), spawn a fresh one with the new token instead of attempting rolling restart.

### Sidecar exists but tunnel isn't in DB
Drift finding; handled by reconciliation not rotation.

### CF API down during rotation
Abort with specific error; next scheduler tick retries. Don't mark tunnel as failed.

### Multiple concurrent rotations
APScheduler `max_instances=1` prevents scheduler overlap. Manual rotation via API uses a tunnel-scoped async lock:

```python
tunnel_locks: dict[int, asyncio.Lock] = {}

async def rotate_with_lock(tunnel_id: int, ...):
    lock = tunnel_locks.setdefault(tunnel_id, asyncio.Lock())
    async with lock:
        return await rotate_tunnel_soft(tunnel_id, ...)
```

### Rotation during DNS conflict resolution
Refuse to start rotation if there's an unresolved conflict for any of the tunnel's routes. User must resolve first.
