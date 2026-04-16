"""Cloudflare-specific internal types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CFAccount:
    id: str
    name: str
    type: str = ""


@dataclass
class CFZone:
    id: str
    name: str
    account_id: str
    plan_name: str = "free"
    status: str = "active"


@dataclass
class CFTunnel:
    id: str
    name: str
    created_at: str = ""
    deleted_at: str | None = None
    connections: list[dict[str, object]] = field(default_factory=list)


@dataclass
class CFDnsRecord:
    id: str
    zone_id: str
    name: str
    type: str
    content: str
    proxied: bool = True
    ttl: int = 1
    comment: str | None = None


@dataclass
class ConflictResult:
    status: (
        str  # "clear" | "conflict_owned_tracked" | "conflict_owned_orphaned" | "conflict_external"
    )
    existing_record: dict[str, object] | None = None
    existing_route_id: int | None = None
