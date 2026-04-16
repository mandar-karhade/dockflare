"""Unified dashboard endpoint — project-centric view merging Docker + CF data."""

from __future__ import annotations

import base64
import json as json_mod
import platform
from typing import Any

import httpx as _httpx
from fastapi import APIRouter

from app.api.deps import get_cf_client, get_docker_client
from app.clients.docker.helpers import get_compose_identity, get_container_name, get_exposed_ports, get_networks
from app.core import labels as lbl

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard() -> dict[str, Any]:
    """Return a unified project-centric view of containers, tunnels, and routes."""
    client = get_cf_client()
    docker = get_docker_client()

    # Detect this server
    local_public_ip: str | None = None
    try:
        async with _httpx.AsyncClient(timeout=5) as http:
            resp = await http.get("https://api.ipify.org")
            local_public_ip = resp.text.strip()
    except Exception:
        pass
    machine = platform.machine()
    local_arch = "linux_amd64" if machine in ("x86_64", "AMD64") else f"linux_{machine}"

    # Get CF data
    accounts = await client.list_accounts()
    account_id = accounts[0]["id"] if accounts else ""
    cf_tunnels = await client.list_tunnels(account_id) if account_id else []

    # Get all containers
    all_containers = await docker.list_containers()

    # Map tunnel_id -> sidecar container info (from local cloudflared containers)
    local_sidecar_for_tunnel: dict[str, dict[str, Any]] = {}
    for attrs in all_containers:
        img = attrs.get("Config", {}).get("Image", "")
        if "cloudflared" not in img.lower():
            continue
        clabels = attrs.get("Config", {}).get("Labels", {})
        tunnel_uuid = clabels.get(lbl.TUNNEL_CF_ID, "")
        if not tunnel_uuid:
            cmd = attrs.get("Config", {}).get("Cmd") or []
            token_val = ""
            for i, arg in enumerate(cmd):
                if arg == "--token" and i + 1 < len(cmd):
                    token_val = cmd[i + 1]
                    break
            if not token_val:
                for env in attrs.get("Config", {}).get("Env") or []:
                    if env.startswith("TUNNEL_TOKEN="):
                        token_val = env.split("=", 1)[1]
                        break
            if token_val:
                try:
                    decoded = base64.b64decode(token_val + "==")
                    token_data = json_mod.loads(decoded)
                    tunnel_uuid = token_data.get("t", "")
                except Exception:
                    pass
        if tunnel_uuid:
            # Use tunnel-manager labels for project if available, fall back to compose labels
            tm_project = clabels.get(lbl.TARGET_PROJECT)
            compose_project = get_compose_identity(attrs)[0]
            local_sidecar_for_tunnel[tunnel_uuid] = {
                "container_name": get_container_name(attrs),
                "project": tm_project or compose_project,
                "networks": get_networks(attrs),
                "status": attrs["State"]["Status"],
            }

    # Build tunnel info with connections + ingress
    tunnel_map: dict[str, dict[str, Any]] = {}  # tunnel_id -> full info
    machines: dict[str, int] = {}

    for t in cf_tunnels:
        tid = t["id"]
        raw_conns = await client.get_tunnel_connections(account_id, tid)
        conns = []
        origin_ip = None
        connector_arch = None
        for connector in raw_conns:
            if not connector_arch:
                connector_arch = connector.get("arch")
            if not origin_ip and connector.get("conns"):
                origin_ip = connector["conns"][0].get("origin_ip")
            for c in connector.get("conns", []):
                conns.append(c)

        ingress_rules = []
        try:
            config = await client.get_tunnel_config(account_id, tid)
            ingress_rules = config.get("config", {}).get("ingress", [])
        except Exception:
            pass

        sidecar = local_sidecar_for_tunnel.get(tid)
        ip_matches = origin_ip and local_public_ip and origin_ip == local_public_ip
        arch_matches = connector_arch and connector_arch == local_arch
        is_local = sidecar is not None or (bool(ip_matches) and bool(arch_matches))

        if origin_ip and connector_arch:
            machine_id = f"{origin_ip} ({connector_arch.replace('linux_', '')})"
        elif origin_ip:
            machine_id = origin_ip
        else:
            machine_id = "unknown"
        machines[machine_id] = machines.get(machine_id, 0) + 1

        # Parse ingress to hostname->service mapping
        routes = []
        for r in ingress_rules:
            if "hostname" not in r:
                continue
            svc_url = r.get("service", "")
            routes.append({
                "hostname": r["hostname"],
                "service": svc_url,
                "path": r.get("path"),
            })

        tunnel_map[tid] = {
            "tunnel_id": tid,
            "name": t["name"],
            "status": "connected" if conns else "disconnected",
            "connections": len(conns),
            "is_local": is_local,
            "machine": machine_id,
            "origin_ip": origin_ip,
            "routes": routes,
            "sidecar": {
                "name": sidecar["container_name"],
                "project": sidecar["project"],
                "networks": sidecar["networks"],
                "status": sidecar["status"],
            } if sidecar else None,
        }

    # Build project-centric view from containers
    projects: dict[str, dict[str, Any]] = {}
    standalone_containers: list[dict[str, Any]] = []

    for attrs in all_containers:
        project, service = get_compose_identity(attrs)
        name = get_container_name(attrs)
        img = attrs.get("Config", {}).get("Image", "")
        is_cf = "cloudflared" in img.lower()
        ports = get_exposed_ports(attrs)
        nets = get_networks(attrs)
        status = attrs["State"]["Status"]

        container_entry: dict[str, Any] = {
            "container_id": attrs["Id"][:12],
            "name": name,
            "service": service,
            "image": img,
            "status": status,
            "ports": ports,
            "networks": nets,
            "is_cloudflared": is_cf,
            "hostname": None,
            "target_service_url": None,
            "tunnel_name": None,
            "tunnel_id": None,
        }

        if not project:
            standalone_containers.append(container_entry)
            continue

        if project not in projects:
            projects[project] = {
                "project": project,
                "networks": [],
                "containers": [],
                "tunnel": None,
            }

        # Collect unique networks at project level
        for n in nets:
            if n != "bridge" and n not in projects[project]["networks"]:
                projects[project]["networks"].append(n)

        projects[project]["containers"].append(container_entry)

    # Now match tunnels to containers by service name within the same project
    # A tunnel's sidecar tells us which project it belongs to
    for tid, tinfo in tunnel_map.items():
        sidecar = tinfo.get("sidecar")
        sidecar_project = sidecar["project"] if sidecar else None

        for route in tinfo["routes"]:
            svc_url = route["service"]
            svc_name = svc_url.replace("http://", "").replace("https://", "").split(":")[0]

            # Try to match to a container in the sidecar's project
            if sidecar_project and sidecar_project in projects:
                for c in projects[sidecar_project]["containers"]:
                    if c["service"] == svc_name or c["name"].endswith(f"-{svc_name}-1") or c["name"].endswith(f"_{svc_name}_1"):
                        c["hostname"] = route["hostname"]
                        c["target_service_url"] = svc_url
                        c["tunnel_name"] = tinfo["name"]
                        c["tunnel_id"] = tid
                        break

        # Attach tunnel info to its project
        if sidecar_project and sidecar_project in projects:
            projects[sidecar_project]["tunnel"] = {
                "tunnel_id": tid,
                "name": tinfo["name"],
                "status": tinfo["status"],
                "connections": tinfo["connections"],
                "machine": tinfo["machine"],
                "route_count": len(tinfo["routes"]),
            }

    return {
        "projects": list(projects.values()),
        "standalone": standalone_containers,
        "tunnels": list(tunnel_map.values()),
        "machines": machines,
        "local_ip": local_public_ip,
        "total_tunnels": len(tunnel_map),
        "total_projects": len(projects),
    }
