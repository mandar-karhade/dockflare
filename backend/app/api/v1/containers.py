"""Container discovery endpoint — lists Docker containers on the host."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import get_cf_client, get_docker_client
from app.clients.docker.helpers import (
    get_compose_identity,
    get_container_name,
    get_exposed_ports,
    get_networks,
    is_managed,
)

router = APIRouter(prefix="/containers", tags=["containers"])


@router.get("")
async def list_containers() -> dict[str, Any]:
    """List all Docker containers grouped by compose project, with route assignments."""
    docker = get_docker_client()
    raw_containers = await docker.list_containers()

    # Build a map of service -> assigned hostnames from CF tunnels
    service_routes: dict[str, list[str]] = {}
    try:
        client = get_cf_client()
        accounts = await client.list_accounts()
        if accounts:
            account_id = accounts[0]["id"]
            tunnels = await client.list_tunnels(account_id)
            for t in tunnels:
                try:
                    config = await client.get_tunnel_config(account_id, t["id"])
                    for rule in config.get("config", {}).get("ingress", []):
                        svc = rule.get("service", "")
                        hostname = rule.get("hostname")
                        if hostname and svc:
                            # Extract service name from URL like "http://n8n-main:5678"
                            svc_key = svc.replace("http://", "").replace("https://", "")
                            service_routes.setdefault(svc_key, []).append(hostname)
                            # Also index by just the host part (without port)
                            svc_host = svc_key.split(":")[0]
                            if svc_host != svc_key:
                                service_routes.setdefault(svc_host, []).append(hostname)
                except Exception:
                    pass
    except Exception:
        pass

    projects: dict[str, list[dict[str, Any]]] = {}
    standalone: list[dict[str, Any]] = []

    for attrs in raw_containers:
        project, service = get_compose_identity(attrs)
        name = get_container_name(attrs)
        ports = get_exposed_ports(attrs)
        networks = get_networks(attrs)

        # Find assigned routes for this service
        assigned_routes: list[str] = []
        lookup_keys = []
        if service:
            lookup_keys.append(service)
            for port in ports:
                lookup_keys.append(f"{service}:{port}")
        lookup_keys.append(name)
        for port in ports:
            lookup_keys.append(f"{name}:{port}")

        for key in lookup_keys:
            for route in service_routes.get(key, []):
                if route not in assigned_routes:
                    assigned_routes.append(route)

        entry: dict[str, Any] = {
            "container_id": attrs["Id"][:12],
            "name": name,
            "compose_project": project,
            "compose_service": service,
            "image": attrs["Config"]["Image"],
            "status": attrs["State"]["Status"],
            "networks": networks,
            "exposed_ports": ports,
            "is_cloudflared": "cloudflared" in attrs["Config"]["Image"].lower(),
            "managed_by_tm": is_managed(attrs),
            "assigned_routes": assigned_routes,
        }

        if project:
            projects.setdefault(project, []).append(entry)
        else:
            standalone.append(entry)

    return {
        "projects": projects,
        "standalone": standalone,
        "total_containers": len(raw_containers),
        "total_projects": len(projects),
        "cloudflared_count": sum(
            1 for group in projects.values() for c in group if c["is_cloudflared"]
        )
        + sum(1 for c in standalone if c["is_cloudflared"]),
    }
