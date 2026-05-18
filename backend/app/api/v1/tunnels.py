"""Tunnel endpoints — discovery, CRUD, and import from CF."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import get_cf_client, get_cf_tunnel_cache

router = APIRouter(prefix="/tunnels", tags=["tunnels"])


async def _default_account_id(client: Any) -> str | None:
    accounts = await client.list_accounts()
    return accounts[0]["id"] if accounts else None


@router.get("")
async def list_tunnels(account_id: str | None = Query(default=None)) -> dict[str, Any]:
    """List all tunnels from Cloudflare with their ingress config."""
    client = get_cf_client()

    if not account_id:
        accounts = await client.list_accounts()
        if not accounts:
            return {"error": "No accounts found", "tunnels": []}
        account_id = accounts[0]["id"]

    raw_tunnels = await client.list_tunnels(account_id)

    # Detect this server's public IP and architecture
    import platform
    import httpx as _httpx

    local_public_ip: str | None = None
    try:
        async with _httpx.AsyncClient(timeout=5) as http:
            resp = await http.get("https://api.ipify.org")
            local_public_ip = resp.text.strip()
    except Exception:
        pass

    # Map host arch to cloudflared's arch naming
    machine = platform.machine()  # x86_64, aarch64, arm64, etc.
    local_arch = "linux_amd64" if machine in ("x86_64", "AMD64") else f"linux_{machine}"

    # Build container lookup: service_url -> container info
    from app.api.deps import get_docker_client
    from app.clients.docker.helpers import get_compose_identity, get_container_name, get_networks

    docker = get_docker_client()
    from app.core import labels as lbl

    import base64
    import json as json_mod

    all_containers = await docker.list_containers()

    # Map tunnel_id -> sidecar info (project, network, container name)
    local_sidecar_info: dict[str, dict[str, Any]] = {}

    for attrs in all_containers:
        image = attrs.get("Config", {}).get("Image", "")
        if "cloudflared" not in image.lower():
            continue

        project, service = get_compose_identity(attrs)
        name = get_container_name(attrs)
        nets = get_networks(attrs)
        container_labels = attrs.get("Config", {}).get("Labels", {})

        # Extract tunnel UUID from manager label or token
        tunnel_uuid = ""
        cf_id = container_labels.get(lbl.TUNNEL_CF_ID, "")
        if cf_id:
            tunnel_uuid = cf_id
        else:
            cmd = attrs.get("Config", {}).get("Cmd") or []
            token_value = ""
            for i, arg in enumerate(cmd):
                if arg == "--token" and i + 1 < len(cmd):
                    token_value = cmd[i + 1]
                    break
            if not token_value:
                for env in attrs.get("Config", {}).get("Env") or []:
                    if env.startswith("TUNNEL_TOKEN="):
                        token_value = env.split("=", 1)[1]
                        break
            if token_value:
                try:
                    decoded = base64.b64decode(token_value + "==")
                    token_data = json_mod.loads(decoded)
                    tunnel_uuid = token_data.get("t", "")
                except Exception:
                    pass

        if tunnel_uuid:
            local_sidecar_info[tunnel_uuid] = {
                "sidecar_name": name,
                "compose_project": project,
                "networks": [n for n in nets if n != "bridge"] or nets,
                "status": attrs["State"]["Status"],
            }

    tunnels: list[dict[str, Any]] = []

    for t in raw_tunnels:
        tunnel_id = t["id"]
        raw_conns = await client.get_tunnel_connections(account_id, tunnel_id)

        # The connections endpoint returns a list of connector objects,
        # each with a "conns" array of individual connections
        conns: list[dict[str, Any]] = []
        origin_ip: str | None = None
        connector_arch: str | None = None
        for connector in raw_conns:
            if not connector_arch:
                connector_arch = connector.get("arch")
            if not origin_ip:
                origin_ip = connector.get("conns", [{}])[0].get("origin_ip") if connector.get("conns") else None
            for c in connector.get("conns", []):
                conns.append(c)

        ingress_rules: list[dict[str, Any]] = []
        try:
            config = await client.get_tunnel_config(account_id, tunnel_id)
            ingress_rules = config.get("config", {}).get("ingress", [])
        except Exception:
            pass

        sidecar = local_sidecar_info.get(tunnel_id)

        # Build a machine identifier from origin_ip + arch
        if origin_ip and connector_arch:
            arch_short = connector_arch.replace("linux_", "")
            machine_id = f"{origin_ip} ({arch_short})"
        elif origin_ip:
            machine_id = origin_ip
        else:
            machine_id = "unknown"

        # Is local = sidecar running here, or IP+arch matches this server
        ip_matches = origin_ip is not None and local_public_ip is not None and origin_ip == local_public_ip
        arch_matches = connector_arch is not None and connector_arch == local_arch
        is_local = sidecar is not None or (ip_matches and arch_matches)

        # Simple ingress list — no container matching per route
        ingress: list[dict[str, Any]] = []
        for r in ingress_rules:
            svc_url = r.get("service", "")
            hostname = r.get("hostname")
            svc_parts = svc_url.replace("http://", "").replace("https://", "")
            svc_name = svc_parts.split(":")[0]
            port = svc_parts.split(":")[-1].split("/")[0] if ":" in svc_parts else ""

            ingress.append({
                "hostname": hostname or "(catch-all)",
                "service": svc_url,
                "path": r.get("path"),
                "target_service_name": svc_name,
                "target_port": port,
            })

        tunnels.append({
            "tunnel_id": tunnel_id,
            "name": t["name"],
            "created_at": t.get("created_at", ""),
            "status": "connected" if conns else "disconnected",
            "connections": len(conns),
            "origin_ip": origin_ip,
            "arch": connector_arch,
            "connection_details": [
                {"colo": c.get("colo_name", "?"), "id": c.get("id", "")[:8]}
                for c in conns
            ],
            "ingress": ingress,
            "route_count": len([r for r in ingress_rules if "hostname" in r]),
            "is_local": is_local,
            "machine": machine_id,
            # Sidecar info at the tunnel level (not per-route)
            "sidecar": {
                "name": sidecar["sidecar_name"],
                "project": sidecar["compose_project"],
                "networks": sidecar["networks"],
                "status": sidecar["status"],
            } if sidecar else None,
        })

    # Build machine summary
    machines: dict[str, int] = {}
    for t in tunnels:
        m = t["machine"]
        machines[m] = machines.get(m, 0) + 1

    return {
        "account_id": account_id,
        "local_ip": local_public_ip,
        "local_arch": local_arch,
        "tunnels": tunnels,
        "total": len(tunnels),
        "machines": machines,
        "connected": sum(1 for t in tunnels if t["status"] == "connected"),
        "disconnected": sum(1 for t in tunnels if t["status"] == "disconnected"),
    }


class TunnelCreateRequest(BaseModel):
    name: str
    primary_compose_project: str | None = None
    primary_compose_service: str | None = None


@router.post("")
async def create_tunnel(body: TunnelCreateRequest) -> dict[str, Any]:
    """Create a new CF tunnel and spawn a cloudflared sidecar."""
    client = get_cf_client()

    accounts = await client.list_accounts()
    if not accounts:
        return {"error": "No accounts found"}
    account_id = accounts[0]["id"]

    # Create tunnel in CF
    cf_tunnel = await client.create_tunnel(account_id, body.name)
    tunnel_id = cf_tunnel["id"]

    # Fetch token
    token = await client.get_tunnel_token(account_id, tunnel_id)

    # Spawn cloudflared sidecar
    from app.api.deps import get_docker_client
    from app.clients.docker.helpers import sidecar_name
    from app.core import labels as lbl

    docker = get_docker_client()
    project = body.primary_compose_project or "standalone"
    service = body.primary_compose_service or body.name
    container_name = sidecar_name(project, service)

    # Find target network — search by project+service, then project alone
    networks: list[str] = []
    if body.primary_compose_project:
        from app.clients.docker.helpers import get_networks

        targets = await docker.find_by_compose(
            body.primary_compose_project, body.primary_compose_service
        )
        if targets:
            target_nets = get_networks(targets[0])
            networks.extend(target_nets)
    if not networks:
        networks = ["bridge"]

    sidecar_labels = {
        lbl.TUNNEL_ID: tunnel_id,
        lbl.TUNNEL_CF_ID: tunnel_id,
        lbl.TUNNEL_NAME: body.name,
        lbl.TARGET_PROJECT: project,
        lbl.TARGET_SERVICE: service,
        lbl.SIDECAR_ROLE: "cloudflared-sidecar",
    }

    sidecar = await docker.spawn_cloudflared(
        name=container_name,
        image="cloudflare/cloudflared:latest",
        token=token,
        networks=networks,
        labels=sidecar_labels,
    )
    await get_cf_tunnel_cache().refresh_tunnel(client, account_id, tunnel_id)

    return {
        "tunnel_id": tunnel_id,
        "name": body.name,
        "sidecar_container_id": sidecar["Id"][:12],
        "sidecar_name": container_name,
        "networks": networks,
        "status": "created",
    }


@router.delete("/{tunnel_id}")
async def delete_tunnel(tunnel_id: str) -> dict[str, Any]:
    """Delete a CF tunnel and stop its sidecar."""
    client = get_cf_client()

    accounts = await client.list_accounts()
    if not accounts:
        return {"error": "No accounts found"}
    account_id = accounts[0]["id"]

    # Find and stop sidecar container
    from app.api.deps import get_docker_client
    from app.core import labels as lbl

    docker = get_docker_client()
    sidecars = await docker.find_by_label(lbl.TUNNEL_CF_ID, tunnel_id)
    for s in sidecars:
        cid = s["Id"]
        try:
            await docker.stop_container(cid)
            await docker.remove_container(cid, force=True)
        except Exception:
            pass

    # Delete DNS records for this tunnel's ingress
    try:
        config = await client.get_tunnel_config(account_id, tunnel_id)
        ingress = config.get("config", {}).get("ingress", [])
        zones = await client.list_zones(account_id)
        zone_map = {z["name"]: z["id"] for z in zones}

        for rule in ingress:
            hostname = rule.get("hostname")
            if not hostname:
                continue
            # Find zone for this hostname
            parts = hostname.split(".")
            for i in range(len(parts) - 1):
                candidate = ".".join(parts[i:])
                if candidate in zone_map:
                    zone_id = zone_map[candidate]
                    records = await client.list_dns_records(zone_id, name=hostname)
                    for r in records:
                        if ".cfargotunnel.com" in r.get("content", ""):
                            await client.delete_dns_record(zone_id, r["id"])
                    break
    except Exception:
        pass

    # Delete the tunnel from CF
    await client.delete_tunnel(account_id, tunnel_id)
    get_cf_tunnel_cache().invalidate_tunnel(account_id, tunnel_id)

    return {"tunnel_id": tunnel_id, "status": "deleted"}


@router.post("/{tunnel_id}/refresh")
async def refresh_tunnel(tunnel_id: str) -> dict[str, Any]:
    """Force-refresh one tunnel's cached Cloudflare status and config."""
    client = get_cf_client()
    account_id = await _default_account_id(client)
    if not account_id:
        return {"error": "No accounts found"}

    cached = await get_cf_tunnel_cache().refresh_tunnel(client, account_id, tunnel_id)
    conns = [conn for connector in cached.connections for conn in connector.get("conns", [])]
    ingress = cached.config.get("config", {}).get("ingress", [])
    return {
        "tunnel_id": tunnel_id,
        "name": cached.tunnel["name"],
        "status": "connected" if conns else "disconnected",
        "connections": len(conns),
        "route_count": len([rule for rule in ingress if "hostname" in rule]),
    }


class IngressUpdateRequest(BaseModel):
    hostname: str
    service: str
    path: str | None = None


@router.put("/{tunnel_id}/ingress")
async def update_ingress(
    tunnel_id: str, rules: list[IngressUpdateRequest]
) -> dict[str, Any]:
    """Update ingress and sync DNS CNAMEs for a tunnel."""
    client = get_cf_client()

    accounts = await client.list_accounts()
    if not accounts:
        return {"error": "No accounts found"}
    account_id = accounts[0]["id"]

    # Snapshot existing hostnames so we know what DNS to clean up after the push
    try:
        current_config = await client.get_tunnel_config(account_id, tunnel_id)
        current_ingress = current_config.get("config", {}).get("ingress", [])
    except Exception:
        current_ingress = []
    old_hostnames = {r["hostname"] for r in current_ingress if r.get("hostname")}
    new_hostnames = {r.hostname for r in rules}
    to_remove = old_hostnames - new_hostnames

    ingress: list[dict[str, Any]] = []
    for r in rules:
        rule: dict[str, Any] = {"hostname": r.hostname, "service": r.service}
        if r.path:
            rule["path"] = r.path
        ingress.append(rule)
    # Always add catch-all
    ingress.append({"service": "http_status:404"})

    config = {"ingress": ingress, "warp-routing": {"enabled": False}}
    await client.update_tunnel_config(account_id, tunnel_id, config)

    # Sync DNS: ensure CNAMEs for the current ingress, drop CNAMEs for removed hostnames
    zones = await client.list_zones(account_id)
    zone_map: dict[str, str] = {z["name"]: z["id"] for z in zones}
    cname_target = f"{tunnel_id}.cfargotunnel.com"
    dns_results: list[dict[str, str]] = []

    def _resolve_zone(hostname: str) -> str | None:
        parts = hostname.lstrip("*.").split(".")
        for i in range(len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in zone_map:
                return zone_map[candidate]
        return None

    for hostname in new_hostnames:
        zone_id = _resolve_zone(hostname)
        if not zone_id:
            dns_results.append({"hostname": hostname, "status": "no_zone_found"})
            continue
        existing = await client.list_dns_records(zone_id, name=hostname)
        if any(cname_target in (r.get("content") or "") for r in existing):
            continue
        if existing:
            for rec in existing:
                if rec.get("type") in ("CNAME", "A", "AAAA"):
                    await client.update_dns_record(
                        zone_id, rec["id"],
                        record_type="CNAME",
                        content=cname_target,
                        proxied=True,
                    )
                    dns_results.append({"hostname": hostname, "status": "updated"})
                    break
        else:
            await client.create_dns_record(
                zone_id,
                record_type="CNAME",
                name=hostname,
                content=cname_target,
                proxied=True,
                comment=f"tunnel-manager:{tunnel_id}",
            )
            dns_results.append({"hostname": hostname, "status": "created"})

    for hostname in to_remove:
        zone_id = _resolve_zone(hostname)
        if not zone_id:
            continue
        existing = await client.list_dns_records(zone_id, name=hostname)
        for rec in existing:
            if "cfargotunnel.com" in (rec.get("content") or ""):
                try:
                    await client.delete_dns_record(zone_id, rec["id"])
                    dns_results.append({"hostname": hostname, "status": "deleted"})
                except Exception:
                    pass
    await get_cf_tunnel_cache().refresh_tunnel(client, account_id, tunnel_id)

    return {
        "tunnel_id": tunnel_id,
        "ingress_count": len(rules),
        "dns": dns_results,
        "status": "updated",
    }


class AdoptTunnelRequest(BaseModel):
    tunnel_id: str
    spawn_sidecar: bool = False
    target_compose_project: str | None = None
    target_compose_service: str | None = None


@router.post("/adopt")
async def adopt_tunnel(body: AdoptTunnelRequest) -> dict[str, Any]:
    """Adopt an existing CF tunnel — track it without disrupting current cloudflared."""
    client = get_cf_client()

    accounts = await client.list_accounts()
    if not accounts:
        return {"error": "No accounts found"}
    account_id = accounts[0]["id"]

    # Verify tunnel exists
    tunnel = await client.get_tunnel(account_id, body.tunnel_id)
    conns = await client.get_tunnel_connections(account_id, body.tunnel_id)

    result: dict[str, Any] = {
        "tunnel_id": body.tunnel_id,
        "name": tunnel["name"],
        "connections": len(conns),
        "status": "adopted",
    }

    # Optionally spawn a manager-controlled sidecar
    if body.spawn_sidecar:
        token = await client.get_tunnel_token(account_id, body.tunnel_id)

        from app.api.deps import get_docker_client
        from app.clients.docker.helpers import sidecar_name
        from app.core import labels as lbl

        docker = get_docker_client()
        project = body.target_compose_project or "standalone"
        service = body.target_compose_service or tunnel["name"]
        container_name = sidecar_name(project, service)

        networks: list[str] = []
        if body.target_compose_project:
            from app.clients.docker.helpers import get_networks

            targets = await docker.find_by_compose(
                body.target_compose_project, body.target_compose_service
            )
            if targets:
                target_nets = get_networks(targets[0])
                networks.extend(target_nets)
        if not networks:
            networks = ["bridge"]

        sidecar_labels = {
            lbl.TUNNEL_ID: body.tunnel_id,
            lbl.TUNNEL_CF_ID: body.tunnel_id,
            lbl.TUNNEL_NAME: tunnel["name"],
            lbl.TARGET_PROJECT: project,
            lbl.TARGET_SERVICE: service,
            lbl.SIDECAR_ROLE: "cloudflared-sidecar",
        }

        sidecar = await docker.spawn_cloudflared(
            name=container_name,
            image="cloudflare/cloudflared:latest",
            token=token,
            networks=networks,
            labels=sidecar_labels,
        )
        result["sidecar_container_id"] = sidecar["Id"][:12]
        result["sidecar_name"] = container_name
    await get_cf_tunnel_cache().refresh_tunnel(client, account_id, body.tunnel_id)

    return result


@router.get("/{tunnel_id}/export")
async def export_tunnel_config(tunnel_id: str) -> dict[str, Any]:
    """Export a tunnel's full config (name + ingress rules) for backup/recreation."""
    client = get_cf_client()

    accounts = await client.list_accounts()
    if not accounts:
        return {"error": "No accounts found"}
    account_id = accounts[0]["id"]

    tunnel = await client.get_tunnel(account_id, tunnel_id)

    ingress_rules: list[dict[str, Any]] = []
    try:
        config = await client.get_tunnel_config(account_id, tunnel_id)
        ingress_rules = config.get("config", {}).get("ingress", [])
    except Exception:
        pass

    # Strip catch-all for export (it's auto-added on import)
    exportable_rules = [r for r in ingress_rules if "hostname" in r]

    return {
        "version": 1,
        "tunnel_name": tunnel["name"],
        "tunnel_id": tunnel_id,
        "exported_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
        "ingress": exportable_rules,
    }


class ImportTunnelRequest(BaseModel):
    tunnel_name: str
    ingress: list[dict[str, Any]]
    spawn_sidecar: bool = False
    target_compose_project: str | None = None
    target_compose_service: str | None = None


@router.post("/import")
async def import_tunnel_config(body: ImportTunnelRequest) -> dict[str, Any]:
    """Create a new tunnel from an exported config. Effectively rotates the token."""
    client = get_cf_client()

    accounts = await client.list_accounts()
    if not accounts:
        return {"error": "No accounts found"}
    account_id = accounts[0]["id"]

    # 1. Create new tunnel
    cf_tunnel = await client.create_tunnel(account_id, body.tunnel_name)
    tunnel_id = cf_tunnel["id"]

    # 2. Apply ingress config
    ingress = list(body.ingress)
    ingress.append({"service": "http_status:404"})
    config = {"ingress": ingress, "warp-routing": {"enabled": False}}
    await client.update_tunnel_config(account_id, tunnel_id, config)

    # 3. Create DNS records for each hostname
    zones = await client.list_zones(account_id)
    zone_map: dict[str, str] = {z["name"]: z["id"] for z in zones}
    cname_target = f"{tunnel_id}.cfargotunnel.com"
    dns_results: list[dict[str, str]] = []

    for rule in body.ingress:
        hostname = rule.get("hostname")
        if not hostname:
            continue
        # Find zone
        parts = hostname.split(".")
        zone_id = None
        for i in range(len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in zone_map:
                zone_id = zone_map[candidate]
                break
        if not zone_id:
            dns_results.append({"hostname": hostname, "status": "no_zone_found"})
            continue

        # Check if DNS record already exists
        existing = await client.list_dns_records(zone_id, name=hostname)
        if existing:
            # Update existing record to point to new tunnel
            for rec in existing:
                if rec.get("type") in ("CNAME", "A", "AAAA"):
                    await client.update_dns_record(
                        zone_id, rec["id"],
                        record_type="CNAME",
                        content=cname_target,
                        proxied=True,
                    )
                    dns_results.append({"hostname": hostname, "status": "updated"})
                    break
        else:
            await client.create_dns_record(
                zone_id,
                record_type="CNAME",
                name=hostname,
                content=cname_target,
                proxied=True,
                comment=f"tunnel-manager:{tunnel_id}",
            )
            dns_results.append({"hostname": hostname, "status": "created"})

    result: dict[str, Any] = {
        "tunnel_id": tunnel_id,
        "name": body.tunnel_name,
        "ingress_count": len(body.ingress),
        "dns": dns_results,
        "status": "imported",
    }

    # 4. Optionally spawn sidecar
    if body.spawn_sidecar:
        token = await client.get_tunnel_token(account_id, tunnel_id)

        from app.api.deps import get_docker_client
        from app.clients.docker.helpers import get_networks, sidecar_name
        from app.core import labels as lbl

        docker = get_docker_client()
        project = body.target_compose_project or "standalone"
        service = body.target_compose_service or body.tunnel_name
        container_name = sidecar_name(project, service)

        networks: list[str] = []
        if body.target_compose_project:
            targets = await docker.find_by_compose(
                body.target_compose_project, body.target_compose_service
            )
            if targets:
                target_nets = get_networks(targets[0])
                networks.extend(target_nets)
        if not networks:
            networks = ["bridge"]

        sidecar_labels = {
            lbl.TUNNEL_ID: tunnel_id,
            lbl.TUNNEL_CF_ID: tunnel_id,
            lbl.TUNNEL_NAME: body.tunnel_name,
            lbl.TARGET_PROJECT: project,
            lbl.TARGET_SERVICE: service,
            lbl.SIDECAR_ROLE: "cloudflared-sidecar",
        }

        sidecar = await docker.spawn_cloudflared(
            name=container_name,
            image="cloudflare/cloudflared:latest",
            token=token,
            networks=networks,
            labels=sidecar_labels,
        )
        result["sidecar_container_id"] = sidecar["Id"][:12]
    await get_cf_tunnel_cache().refresh_tunnel(client, account_id, tunnel_id)

    return result


@router.post("/{tunnel_id}/recreate")
async def recreate_tunnel(tunnel_id: str) -> dict[str, Any]:
    """Recreate a tunnel: export config, delete old, create new with same routes + DNS.

    This is effectively token rotation — same name, same routes, fresh tunnel ID and token.
    """
    client = get_cf_client()

    accounts = await client.list_accounts()
    if not accounts:
        return {"error": "No accounts found"}
    account_id = accounts[0]["id"]

    # 1. Export current config
    old_tunnel = await client.get_tunnel(account_id, tunnel_id)
    tunnel_name = old_tunnel["name"]

    ingress_rules: list[dict[str, Any]] = []
    try:
        config = await client.get_tunnel_config(account_id, tunnel_id)
        ingress_rules = [r for r in config.get("config", {}).get("ingress", []) if "hostname" in r]
    except Exception:
        pass

    # 2. Find existing sidecar info before deleting
    from app.api.deps import get_docker_client
    from app.clients.docker.helpers import get_networks, sidecar_name
    from app.core import labels as lbl

    docker = get_docker_client()
    old_sidecars = await docker.find_by_label(lbl.TUNNEL_CF_ID, tunnel_id)
    old_sidecar_project: str | None = None
    old_sidecar_service: str | None = None
    old_sidecar_networks: list[str] = []
    for s in old_sidecars:
        sl = s.get("Config", {}).get("Labels", {})
        old_sidecar_project = sl.get(lbl.TARGET_PROJECT)
        old_sidecar_service = sl.get(lbl.TARGET_SERVICE)
        old_sidecar_networks = [n for n in get_networks(s) if n != "bridge"]

    # 3. Stop and remove old sidecar
    for s in old_sidecars:
        try:
            await docker.stop_container(s["Id"])
            await docker.remove_container(s["Id"], force=True)
        except Exception:
            pass

    # 4. Delete old tunnel from CF
    try:
        await client.delete_tunnel(account_id, tunnel_id)
    except Exception:
        pass

    # 5. Create new tunnel with same name
    new_tunnel = await client.create_tunnel(account_id, tunnel_name)
    new_tunnel_id = new_tunnel["id"]

    # 6. Apply same ingress config
    full_ingress = list(ingress_rules)
    full_ingress.append({"service": "http_status:404"})
    new_config = {"ingress": full_ingress, "warp-routing": {"enabled": False}}
    await client.update_tunnel_config(account_id, new_tunnel_id, new_config)

    # 7. Update DNS records to point to new tunnel
    zones = await client.list_zones(account_id)
    zone_map: dict[str, str] = {z["name"]: z["id"] for z in zones}
    new_cname = f"{new_tunnel_id}.cfargotunnel.com"
    dns_updates: list[dict[str, str]] = []

    for rule in ingress_rules:
        hostname = rule.get("hostname")
        if not hostname:
            continue
        parts = hostname.split(".")
        zone_id = None
        for i in range(len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in zone_map:
                zone_id = zone_map[candidate]
                break
        if not zone_id:
            dns_updates.append({"hostname": hostname, "status": "no_zone"})
            continue

        existing = await client.list_dns_records(zone_id, name=hostname)
        if existing:
            for rec in existing:
                if rec.get("type") in ("CNAME", "A", "AAAA"):
                    await client.update_dns_record(zone_id, rec["id"], record_type="CNAME", content=new_cname, proxied=True)
                    dns_updates.append({"hostname": hostname, "status": "updated"})
                    break
        else:
            await client.create_dns_record(zone_id, record_type="CNAME", name=hostname, content=new_cname, proxied=True, comment=f"tunnel-manager:{new_tunnel_id}")
            dns_updates.append({"hostname": hostname, "status": "created"})

    # 8. Spawn new sidecar
    token = await client.get_tunnel_token(account_id, new_tunnel_id)
    project = old_sidecar_project or "standalone"
    service = old_sidecar_service or tunnel_name
    container_name = sidecar_name(project, service)

    networks = old_sidecar_networks if old_sidecar_networks else ["bridge"]
    if not old_sidecar_networks and old_sidecar_project:
        targets = await docker.find_by_compose(old_sidecar_project, old_sidecar_service)
        if targets:
            networks = [n for n in get_networks(targets[0]) if n != "bridge"] or ["bridge"]

    sidecar_labels = {
        lbl.TUNNEL_ID: new_tunnel_id,
        lbl.TUNNEL_CF_ID: new_tunnel_id,
        lbl.TUNNEL_NAME: tunnel_name,
        lbl.TARGET_PROJECT: project,
        lbl.TARGET_SERVICE: service,
        lbl.SIDECAR_ROLE: "cloudflared-sidecar",
    }

    new_sidecar = await docker.spawn_cloudflared(
        name=container_name,
        image="cloudflare/cloudflared:latest",
        token=token,
        networks=networks,
        labels=sidecar_labels,
    )
    get_cf_tunnel_cache().invalidate_tunnel(account_id, tunnel_id)
    await get_cf_tunnel_cache().refresh_tunnel(client, account_id, new_tunnel_id)

    return {
        "old_tunnel_id": tunnel_id,
        "new_tunnel_id": new_tunnel_id,
        "name": tunnel_name,
        "ingress_count": len(ingress_rules),
        "dns": dns_updates,
        "sidecar_container_id": new_sidecar["Id"][:12],
        "status": "recreated",
    }
