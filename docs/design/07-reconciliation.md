# 07 — Reconciliation, Drift, and Conflicts

## Overview

Three distinct but related subsystems:

1. **Conflict detection** — pre-flight checks before creating/modifying DNS records
2. **Drift detection** — periodic scans comparing DB state vs CF/Docker reality
3. **Orphan handling** — finding and cleaning resources with no DB counterpart

## Conflict Detection (Pre-flight)

Runs before any route creation or DNS modification. Goal: avoid silently overwriting records the user didn't explicitly agree to replace.

### Conflict Types

| Type | Description | Default Action |
|------|-------------|----------------|
| `clear` | No existing record for hostname | Proceed |
| `conflict_owned_tracked` | Our record for a known route (e.g., same hostname already mapped) | Ask user to reassign |
| `conflict_owned_orphaned` | Our prefix but no DB row | Offer adopt or delete |
| `conflict_external_cname` | External CNAME to something else | Ask with backup option |
| `conflict_external_a` | External A/AAAA record | Ask with backup option (strong warning) |

### Detection Implementation

```python
async def detect_dns_conflict(hostname: str, zone_id: str) -> ConflictResult:
    existing_records = await cf.dns.records.list(
        zone_id=zone_id,
        name=hostname,
        match="all"
    )
    
    # Filter to types that would conflict
    conflicting_types = {"A", "AAAA", "CNAME"}
    conflicts = [r for r in existing_records if r["type"] in conflicting_types]
    
    if not conflicts:
        return ConflictResult(status="clear")
    
    if len(conflicts) > 1:
        # Multiple records at same name (possible if CF allows legacy config)
        return ConflictResult(
            status="conflict_multiple_records",
            existing_records=conflicts
        )
    
    record = conflicts[0]
    comment = record.get("comment") or ""
    
    if comment.startswith("tunnel-manager:"):
        route_id = parse_route_id_from_comment(comment)
        if route_id:
            tracked_route = db.get_route(route_id)
            if tracked_route:
                return ConflictResult(
                    status="conflict_owned_tracked",
                    existing_route=tracked_route,
                    existing_record=record
                )
        return ConflictResult(
            status="conflict_owned_orphaned",
            existing_record=record
        )
    
    # External record
    if record["type"] in ("A", "AAAA"):
        return ConflictResult(
            status="conflict_external_a",
            existing_record=record,
            severity="high"
        )
    else:  # CNAME
        return ConflictResult(
            status="conflict_external_cname",
            existing_record=record,
            severity="medium"
        )
```

### Resolution Options

When a conflict is detected, the API returns 409 with `resolution_options`:

```json
{
  "type": "https://tunnel-manager.dev/errors/dns-conflict",
  "status": 409,
  "code": "conflict_external_a",
  "detail": "An A record for app.example.com points to 192.0.2.1",
  "existing_record": {
    "type": "A",
    "content": "192.0.2.1",
    "created_on": "2024-03-15T12:00:00Z",
    "comment": "Migrated from legacy server"
  },
  "resolution_options": [
    {
      "action": "replace_with_backup",
      "label": "Replace record (back up original)",
      "recommended": true
    },
    {
      "action": "replace_no_backup",
      "label": "Replace without backup (destructive)",
      "warning": "Cannot be undone"
    },
    {
      "action": "abort",
      "label": "Cancel route creation"
    }
  ]
}
```

Client re-POSTs with `conflict_resolution: "replace_with_backup"` to proceed.

### Resolution Actions

```python
async def resolve_conflict(
    hostname: str,
    zone_id: str,
    resolution: str,
    new_content: str,
    new_comment: str
) -> dict:
    """Returns the resulting DNS record, or raises."""
    
    existing = await cf.dns.records.list(zone_id=zone_id, name=hostname)
    existing = existing[0] if existing else None
    
    if resolution == "abort":
        raise UserAbortError()
    
    if resolution == "replace_with_backup":
        if existing:
            backup = await dns_backup_service.snapshot(existing)
            await audit_log_backup(backup, existing, reason="conflict_resolution")
        if existing:
            return await cf.dns.records.update(
                zone_id, existing["id"],
                type="CNAME",
                name=hostname,
                content=new_content,
                proxied=True,
                comment=new_comment
            )
        else:
            return await cf.dns.records.create(
                zone_id, name=hostname, type="CNAME",
                content=new_content, proxied=True, comment=new_comment
            )
    
    if resolution == "replace_no_backup":
        if existing:
            return await cf.dns.records.update(
                zone_id, existing["id"],
                type="CNAME",
                name=hostname,
                content=new_content,
                proxied=True,
                comment=new_comment
            )
        return await cf.dns.records.create(...)
    
    if resolution == "adopt":
        # The existing record is tunnel-manager's but orphaned; claim it
        return await adopt_orphan_record(existing)
    
    if resolution == "reassign":
        # Move hostname from one tunnel to another
        return await reassign_route_to_new_tunnel(
            old_route_id=existing_route.id,
            new_content=new_content
        )
    
    raise ValueError(f"Unknown resolution: {resolution}")
```

## Drift Detection (Periodic Scan)

Runs hourly by default; also triggerable on demand. Compares each DB entity against its CF counterpart.

### Scan Algorithm

```python
async def run_drift_scan() -> str:
    """Returns scan_id for results lookup."""
    scan_id = str(uuid.uuid4())
    findings: list[DriftFinding] = []
    
    # Phase 1: Check tunnels
    for tunnel in db.query_tunnels(status="active"):
        findings.extend(await check_tunnel_drift(tunnel))
    
    # Phase 2: Check routes (DNS + ingress)
    for route in db.query_routes(status="active"):
        findings.extend(await check_route_drift(route))
    
    # Phase 3: Check sidecar health
    findings.extend(await check_sidecar_drift())
    
    # Persist findings
    for f in findings:
        f.scan_id = scan_id
        db.insert_drift_finding(f)
    
    # Notify if new findings
    if findings:
        await notify_drift_detected(scan_id, len(findings))
    
    return scan_id

async def check_tunnel_drift(tunnel: Tunnel) -> list[DriftFinding]:
    findings = []
    
    # Verify CF tunnel still exists
    try:
        cf_tunnel = await cf.get_tunnel(tunnel.account_id, tunnel.cf_tunnel_id)
    except CFNotFoundError:
        findings.append(DriftFinding(
            finding_type="tunnel_missing",
            entity_type="tunnel",
            entity_id=tunnel.id,
            severity="error",
            expected_value=tunnel.cf_tunnel_id,
            actual_value=None
        ))
        return findings
    
    # Verify tunnel name matches
    if cf_tunnel["name"] != tunnel.cf_tunnel_name:
        findings.append(DriftFinding(
            finding_type="tunnel_renamed",
            entity_type="tunnel",
            entity_id=tunnel.id,
            severity="warning",
            expected_value=tunnel.cf_tunnel_name,
            actual_value=cf_tunnel["name"]
        ))
    
    # Check ingress config matches DB
    cf_config = await cf.get_tunnel_config(tunnel.account_id, tunnel.cf_tunnel_id)
    db_config = build_ingress_from_db(tunnel.id)
    if not ingress_rules_equivalent(cf_config, db_config):
        findings.append(DriftFinding(
            finding_type="ingress_drift",
            entity_type="tunnel",
            entity_id=tunnel.id,
            severity="warning",
            expected_value=json.dumps(db_config, sort_keys=True),
            actual_value=json.dumps(cf_config, sort_keys=True),
            details_json={"diff": diff_ingress(cf_config, db_config)}
        ))
    
    return findings

async def check_route_drift(route: Route) -> list[DriftFinding]:
    findings = []
    
    if not route.cf_dns_record_id:
        return []  # Not yet created; not a drift case
    
    try:
        cf_record = await cf.get_dns_record(route.zone_id, route.cf_dns_record_id)
    except CFNotFoundError:
        findings.append(DriftFinding(
            finding_type="dns_missing",
            entity_type="route",
            entity_id=route.id,
            severity="error",
            expected_value=f"CNAME {route.hostname}",
            actual_value=None
        ))
        return findings
    
    expected_content = f"{route.tunnel.cf_tunnel_id}.cfargotunnel.com"
    if cf_record["content"] != expected_content:
        findings.append(DriftFinding(
            finding_type="dns_content_changed",
            entity_type="route",
            entity_id=route.id,
            severity="error",
            expected_value=expected_content,
            actual_value=cf_record["content"]
        ))
    
    if cf_record["name"] != route.hostname:
        findings.append(DriftFinding(
            finding_type="dns_hostname_changed",
            entity_type="route",
            entity_id=route.id,
            severity="warning",
            expected_value=route.hostname,
            actual_value=cf_record["name"]
        ))
    
    if cf_record.get("proxied") is False and route.dns_proxied:
        findings.append(DriftFinding(
            finding_type="dns_unproxied",
            entity_type="route",
            entity_id=route.id,
            severity="error",  # unproxied breaks tunnel routing
            expected_value="proxied=true",
            actual_value="proxied=false"
        ))
    
    return findings

async def check_sidecar_drift() -> list[DriftFinding]:
    findings = []
    
    for tunnel in db.query_tunnels(status="active"):
        if not tunnel.cloudflared_container_id:
            findings.append(DriftFinding(
                finding_type="sidecar_not_tracked",
                entity_type="tunnel",
                entity_id=tunnel.id,
                severity="warning"
            ))
            continue
        
        try:
            container = docker.containers.get(tunnel.cloudflared_container_id)
        except docker.errors.NotFound:
            findings.append(DriftFinding(
                finding_type="sidecar_missing",
                entity_type="tunnel",
                entity_id=tunnel.id,
                severity="error"
            ))
            continue
        
        if container.status != "running":
            findings.append(DriftFinding(
                finding_type="sidecar_not_running",
                entity_type="tunnel",
                entity_id=tunnel.id,
                severity="error",
                actual_value=container.status
            ))
    
    return findings
```

### Resolution Actions

Each finding has resolution actions in the GUI:

```python
async def resolve_drift(finding_id: int, action: str, actor: str):
    finding = db.get_drift_finding(finding_id)
    if finding.resolved_at:
        raise AlreadyResolvedError()
    
    handler = {
        "ingress_drift": resolve_ingress_drift,
        "dns_content_changed": resolve_dns_content_drift,
        "dns_missing": resolve_dns_missing,
        "dns_unproxied": resolve_dns_unproxied,
        "tunnel_missing": resolve_tunnel_missing,
        "sidecar_missing": resolve_sidecar_missing,
        # ...
    }.get(finding.finding_type)
    
    if not handler:
        raise UnsupportedDriftTypeError()
    
    await handler(finding, action, actor)
    
    db.update_drift_finding(finding.id,
        resolution=action,
        resolved_at=datetime.utcnow(),
        resolved_by=actor
    )

async def resolve_ingress_drift(finding, action, actor):
    tunnel = db.get_tunnel(finding.entity_id)
    
    if action == "reconcile_to_db":
        # Push DB's version of ingress to CF
        await route_service.sync_tunnel_ingress(tunnel.id)
    
    elif action == "accept_external":
        # CF's version is authoritative; update DB routes to match
        cf_config = await cf.get_tunnel_config(tunnel.account_id, tunnel.cf_tunnel_id)
        await route_service.rebuild_routes_from_ingress(tunnel.id, cf_config)
    
    elif action == "ignore":
        pass  # just mark resolved

async def resolve_dns_content_drift(finding, action, actor):
    route = db.get_route(finding.entity_id)
    
    if action == "reconcile_to_db":
        # Fix the DNS record to point at our tunnel
        expected_content = f"{route.tunnel.cf_tunnel_id}.cfargotunnel.com"
        await cf.update_dns_record(
            route.zone_id,
            route.cf_dns_record_id,
            content=expected_content,
            proxied=True
        )
    
    elif action == "accept_external":
        # The DNS record was intentionally changed; route is no longer ours
        db.update_route(route.id, status="disabled",
            status_detail="External DNS change accepted")

async def resolve_sidecar_missing(finding, action, actor):
    tunnel = db.get_tunnel(finding.entity_id)
    if action == "recreate":
        await tunnel_service.create_sidecar(tunnel.id)
    elif action == "disable_tunnel":
        db.update_tunnel(tunnel.id, status="disabled")
```

## Orphan Detection

Orphans are resources with `tunnel-manager:` ownership markers but no corresponding DB row.

### Orphan DNS Record Scan

```python
async def scan_orphan_dns_records(zone_id: str | None = None) -> list[dict]:
    """Find DNS records with tunnel-manager prefix not in DB."""
    orphans = []
    
    zones = [zone_id] if zone_id else [z.zone_id for z in db.get_zones()]
    
    for zid in zones:
        records = await cf.list_all_dns_records(zid)
        for record in records:
            comment = record.get("comment") or ""
            if not comment.startswith("tunnel-manager:"):
                continue
            
            route_id = parse_route_id_from_comment(comment)
            if route_id is None:
                orphans.append({"record": record, "reason": "malformed_comment"})
                continue
            
            tracked = db.get_route(route_id)
            if not tracked:
                orphans.append({"record": record, "reason": "route_not_in_db"})
                continue
            
            if tracked.cf_dns_record_id != record["id"]:
                orphans.append({"record": record, "reason": "id_mismatch"})
    
    return orphans
```

### Orphan Sidecar Scan

```python
async def scan_orphan_sidecars() -> list[dict]:
    orphans = []
    all_managed = docker.containers.list(
        all=True, filters={"label": "tunnel-manager.managed=true"}
    )
    for c in all_managed:
        tunnel_id_str = c.labels.get("tunnel-manager.tunnel.id")
        if not tunnel_id_str or not db.get_tunnel(int(tunnel_id_str)):
            orphans.append({
                "container_id": c.id,
                "name": c.name,
                "labels": dict(c.labels),
                "reason": "tunnel_not_in_db" if tunnel_id_str else "no_tunnel_label"
            })
    return orphans
```

### Orphan CF Tunnel Scan

```python
async def scan_orphan_cf_tunnels(account_id: str) -> list[dict]:
    """CF tunnels that aren't in DB."""
    cf_tunnels = await cf.list_tunnels(account_id)
    db_tunnel_cf_ids = {t.cf_tunnel_id for t in db.query_tunnels()}
    
    orphans = []
    for t in cf_tunnels:
        if t["id"] not in db_tunnel_cf_ids:
            # Verify it's plausibly ours (by name pattern or comment)
            # Or just surface all untracked CF tunnels for user decision
            orphans.append({
                "cf_tunnel_id": t["id"],
                "cf_tunnel_name": t["name"],
                "created_at": t["created_at"],
                "connections": t.get("connections", [])
            })
    return orphans
```

## Adopt Flow (Migration / Recovery)

Used to import existing CF infrastructure into the manager without recreation.

### Adopt a CF Tunnel

```python
async def adopt_cf_tunnel(account_id: str, cf_tunnel_id: str) -> Tunnel:
    # 1. Fetch CF tunnel details
    cf_tunnel = await cf.get_tunnel(account_id, cf_tunnel_id)
    if not cf_tunnel:
        raise NotFoundError()
    
    # 2. Fetch tunnel token
    token = await cf.get_tunnel_token(account_id, cf_tunnel_id)
    
    # 3. Fetch ingress config
    cf_config = await cf.get_tunnel_config(account_id, cf_tunnel_id)
    
    # 4. Try to infer primary target from tunnel name or ingress rules
    primary_project, primary_service = infer_primary_target(cf_tunnel["name"], cf_config)
    
    # 5. Create DB entry
    tunnel = db.create_tunnel(
        cf_tunnel_id=cf_tunnel_id,
        cf_tunnel_name=cf_tunnel["name"],
        account_id=account_id,
        token_encrypted=vault.encrypt(token),
        token_last_four=token[-4:],
        token_fetched_at=datetime.utcnow(),
        primary_compose_project=primary_project,
        primary_compose_service=primary_service,
        status="active"
    )
    
    # 6. Create routes from ingress rules
    for rule in cf_config.get("config", {}).get("ingress", []):
        if "hostname" not in rule:
            continue  # skip catch-all
        
        await adopt_ingress_rule_as_route(tunnel.id, rule)
    
    # 7. Try to find existing cloudflared sidecar for this tunnel
    #    (In the user's case: their existing compose-defined cloudflared)
    existing_sidecar = await find_running_sidecar_for_tunnel(cf_tunnel_id)
    
    if existing_sidecar:
        # User can either "take over" (spawn our own, tear down theirs)
        # or just record the existing one (won't respond to our lifecycle ops)
        db.update_tunnel(tunnel.id,
            cloudflared_container_id=existing_sidecar.id,
            status_detail="Adopted existing sidecar"
        )
    else:
        # Spawn our own sidecar
        await tunnel_service.create_sidecar(tunnel.id)
    
    return tunnel

async def adopt_ingress_rule_as_route(tunnel_id: int, rule: dict) -> Route:
    hostname = rule["hostname"]
    service_url = rule.get("service", "")
    
    # Parse service URL
    scheme, host, port, path = parse_service_url(service_url)
    
    # Try to identify the target container by name
    target_container = docker.containers.list(
        filters={"name": host}, all=True
    )
    if target_container:
        c = target_container[0]
        target_project = c.labels.get("com.docker.compose.project")
        target_service = c.labels.get("com.docker.compose.service")
    else:
        target_project = None
        target_service = None
    
    # Find DNS record for this hostname
    zone_id, zone_name = await resolve_zone(hostname)
    dns_records = await cf.dns.records.list(zone_id=zone_id, name=hostname)
    dns_record_id = dns_records[0]["id"] if dns_records else None
    
    route = db.create_route(
        tunnel_id=tunnel_id,
        hostname=hostname,
        path_regex=rule.get("path"),
        priority=calculate_priority(hostname, rule.get("path")),
        target_compose_project=target_project,
        target_compose_service=target_service,
        target_container_name=host if not target_project else None,
        target_scheme=scheme,
        target_port=port,
        target_path_prefix=path if path != "/" else None,
        zone_id=zone_id,
        zone_name=zone_name,
        cf_dns_record_id=dns_record_id,
        dns_proxied=True,
        origin_options=parse_origin_request(rule.get("originRequest", {})),
        status="adopted",
        enabled=True
    )
    
    # Update DNS record comment to link back to route
    if dns_record_id:
        await cf.update_dns_record(
            zone_id, dns_record_id,
            comment=f"tunnel-manager:route_id={route.id}"
        )
    
    return route
```

### Bulk Import on Bootstrap

```python
async def bootstrap_scan() -> dict:
    """First-run: scan CF account for all existing tunnels."""
    account_id = app_settings.get("active_account_id")
    
    tunnels = await cf.list_tunnels(account_id)
    zones = await cf.list_zones(account_id)
    
    preview = []
    for t in tunnels:
        if t.get("deleted_at"):
            continue
        config = await cf.get_tunnel_config(account_id, t["id"])
        ingress_rules = config.get("config", {}).get("ingress", [])
        hostnames = [r["hostname"] for r in ingress_rules if "hostname" in r]
        
        preview.append({
            "cf_tunnel_id": t["id"],
            "cf_tunnel_name": t["name"],
            "hostnames": hostnames,
            "connections": await cf.get_tunnel_connections(account_id, t["id"])
        })
    
    return {
        "tunnels": preview,
        "zones": [{"zone_id": z["id"], "zone_name": z["name"]} for z in zones]
    }

async def bootstrap_import(
    tunnel_ids: list[str],
    take_over_sidecars: bool = False
) -> dict:
    results = []
    for cf_tunnel_id in tunnel_ids:
        try:
            tunnel = await adopt_cf_tunnel(account_id, cf_tunnel_id)
            results.append({"cf_tunnel_id": cf_tunnel_id, "status": "imported",
                            "db_id": tunnel.id})
        except Exception as e:
            results.append({"cf_tunnel_id": cf_tunnel_id, "status": "failed",
                            "error": str(e)})
    
    return {"results": results}
```

## Policy Configuration

User-configurable defaults in `app_settings`:

```json
{
  "policy.dns_conflict_default": "ask",
  // Values: "ask" (always prompt), "backup_replace" (auto with backup),
  //         "refuse" (never overwrite external records)
  
  "policy.orphan_dns_default": "ask",
  // Values: "ask", "auto_adopt", "auto_delete"
  
  "policy.drift_default": "alert",
  // Values: "alert" (notify, no auto), "auto_reconcile_to_db",
  //         "auto_accept_external"
  
  "policy.stale_backup_retention_days": 90,
  
  "policy.require_confirmation_for_destructive": true
}
```

## Notification Flow

When new drift findings are detected:

1. Insert into `drift_findings` table
2. WebSocket push: `{"type": "drift.detected", "count": N}`
3. Badge on GUI dashboard increments
4. Optional webhook call (if configured)
5. Email alert (if configured and severity >= warning)

## Idempotency

All resolution actions must be safe to retry. Use deterministic logic:

```python
async def reconcile_to_db_idempotent(tunnel_id: int):
    tunnel = db.get_tunnel(tunnel_id)
    expected_config = build_ingress_from_db(tunnel_id)
    current_config = await cf.get_tunnel_config(tunnel.account_id, tunnel.cf_tunnel_id)
    
    if ingress_rules_equivalent(expected_config, current_config):
        return  # already reconciled
    
    await cf.update_tunnel_config(
        tunnel.account_id, tunnel.cf_tunnel_id, expected_config
    )
```

Safe to call N times.
