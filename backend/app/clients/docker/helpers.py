"""Common Docker inspect/filter logic."""

from __future__ import annotations

from typing import Any

from app.core import labels as lbl


def get_compose_identity(attrs: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract (project, service) from container attrs."""
    labels = attrs.get("Config", {}).get("Labels", {})
    return labels.get(lbl.COMPOSE_PROJECT), labels.get(lbl.COMPOSE_SERVICE)


def get_container_name(attrs: dict[str, Any]) -> str:
    """Extract container name without leading slash."""
    name: str = attrs.get("Name", "")
    return name.lstrip("/")


def get_networks(attrs: dict[str, Any]) -> list[str]:
    """Get list of network names a container is attached to."""
    networks = attrs.get("NetworkSettings", {}).get("Networks", {})
    return list(networks.keys())


def get_exposed_ports(attrs: dict[str, Any]) -> list[int]:
    """Get TCP ports exposed by the container's image."""
    exposed = attrs.get("Config", {}).get("ExposedPorts") or {}
    ports: list[int] = []
    for key in exposed:
        port_str, sep, proto = key.partition("/")
        if sep and proto == "tcp" and port_str.isdigit():
            ports.append(int(port_str))
    return ports


def is_managed(attrs: dict[str, Any]) -> bool:
    """Check if a container is managed by tunnel-manager."""
    labels = attrs.get("Config", {}).get("Labels", {})
    return labels.get(lbl.MANAGED) == "true"


def sidecar_name(project: str, service: str, suffix: str = "") -> str:
    """Generate a standardized sidecar container name."""
    base = f"cftunnel-{project}-{service}"
    if suffix:
        base = f"{base}-{suffix}"
    return base
