"""Fake CloudflareClient for testing — in-memory state mimicking CF API."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.errors import CFAPIError


class FakeCloudflareClient:
    """In-memory fake that implements the same interface as CloudflareClient."""

    def __init__(self) -> None:
        self._accounts: dict[str, dict[str, Any]] = {}
        self._zones: dict[str, dict[str, Any]] = {}
        self._tunnels: dict[str, dict[str, Any]] = {}  # keyed by tunnel_id
        self._tunnel_tokens: dict[str, str] = {}  # tunnel_id -> token
        self._tunnel_configs: dict[str, dict[str, Any]] = {}  # tunnel_id -> config
        self._tunnel_connections: dict[str, list[dict[str, Any]]] = {}
        self._dns_records: dict[str, dict[str, Any]] = {}  # record_id -> record

    # --- Seeding ---

    def seed_account(self, account_id: str, name: str) -> None:
        self._accounts[account_id] = {"id": account_id, "name": name, "type": "standard"}

    def seed_zone(self, zone_id: str, zone_name: str, account_id: str) -> None:
        self._zones[zone_id] = {
            "id": zone_id,
            "name": zone_name,
            "account": {"id": account_id},
            "plan": {"name": "free"},
            "status": "active",
        }

    def seed_dns_record(
        self,
        zone_id: str,
        *,
        name: str,
        record_type: str = "CNAME",
        content: str = "",
        proxied: bool = True,
        comment: str | None = None,
        record_id: str | None = None,
    ) -> dict[str, Any]:
        rid = record_id or str(uuid.uuid4())
        record: dict[str, Any] = {
            "id": rid,
            "zone_id": zone_id,
            "name": name,
            "type": record_type,
            "content": content,
            "proxied": proxied,
            "ttl": 1,
            "comment": comment,
        }
        self._dns_records[rid] = record
        return record

    # --- Token verification ---

    async def verify_token(self) -> dict[str, Any]:
        return {"status": "active"}

    async def close(self) -> None:
        pass

    # --- Accounts ---

    async def list_accounts(self) -> list[dict[str, Any]]:
        return list(self._accounts.values())

    # --- Zones ---

    async def list_zones(self, account_id: str) -> list[dict[str, Any]]:
        return [z for z in self._zones.values() if z["account"]["id"] == account_id]

    # --- Tunnels ---

    async def list_tunnels(self, account_id: str) -> list[dict[str, Any]]:
        return [
            t
            for t in self._tunnels.values()
            if t.get("account_id") == account_id and t.get("deleted_at") is None
        ]

    async def create_tunnel(self, account_id: str, name: str) -> dict[str, Any]:
        tunnel_id = str(uuid.uuid4())
        token = f"fake-token-{tunnel_id[:8]}"
        tunnel: dict[str, Any] = {
            "id": tunnel_id,
            "name": name,
            "account_id": account_id,
            "created_at": "2024-01-01T00:00:00Z",
            "deleted_at": None,
            "status": "inactive",
        }
        self._tunnels[tunnel_id] = tunnel
        self._tunnel_tokens[tunnel_id] = token
        self._tunnel_configs[tunnel_id] = {
            "config": {
                "ingress": [{"service": "http_status:404"}],
                "warp-routing": {"enabled": False},
            }
        }
        self._tunnel_connections[tunnel_id] = []
        return tunnel

    async def get_tunnel(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        if tunnel_id not in self._tunnels:
            raise CFAPIError(cf_code=1001, message=f"Tunnel {tunnel_id} not found")
        return self._tunnels[tunnel_id]

    async def delete_tunnel(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        if tunnel_id not in self._tunnels:
            raise CFAPIError(cf_code=1001, message=f"Tunnel {tunnel_id} not found")
        tunnel = self._tunnels.pop(tunnel_id)
        self._tunnel_tokens.pop(tunnel_id, None)
        self._tunnel_configs.pop(tunnel_id, None)
        self._tunnel_connections.pop(tunnel_id, None)
        return tunnel

    async def get_tunnel_token(self, account_id: str, tunnel_id: str) -> str:
        if tunnel_id not in self._tunnel_tokens:
            raise CFAPIError(cf_code=1001, message=f"Tunnel {tunnel_id} not found")
        return self._tunnel_tokens[tunnel_id]

    async def get_tunnel_connections(self, account_id: str, tunnel_id: str) -> list[dict[str, Any]]:
        return self._tunnel_connections.get(tunnel_id, [])

    # --- Tunnel config ---

    async def get_tunnel_config(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        if tunnel_id not in self._tunnel_configs:
            raise CFAPIError(cf_code=1001, message=f"Tunnel {tunnel_id} config not found")
        return self._tunnel_configs[tunnel_id]

    async def update_tunnel_config(
        self, account_id: str, tunnel_id: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        self._tunnel_configs[tunnel_id] = {"config": config}
        return self._tunnel_configs[tunnel_id]

    # --- DNS ---

    async def list_dns_records(
        self, zone_id: str, name: str | None = None, record_type: str | None = None
    ) -> list[dict[str, Any]]:
        results = [r for r in self._dns_records.values() if r["zone_id"] == zone_id]
        if name:
            results = [r for r in results if r["name"] == name]
        if record_type:
            types = set(record_type.split(","))
            results = [r for r in results if r["type"] in types]
        return results

    async def create_dns_record(
        self,
        zone_id: str,
        *,
        record_type: str,
        name: str,
        content: str,
        proxied: bool = True,
        ttl: int = 1,
        comment: str | None = None,
    ) -> dict[str, Any]:
        # Check for duplicate
        existing = [
            r
            for r in self._dns_records.values()
            if r["zone_id"] == zone_id and r["name"] == name and r["type"] == record_type
        ]
        if existing:
            raise CFAPIError(cf_code=81057, message=f"Record already exists for {name}")

        record_id = str(uuid.uuid4())
        record: dict[str, Any] = {
            "id": record_id,
            "zone_id": zone_id,
            "name": name,
            "type": record_type,
            "content": content,
            "proxied": proxied,
            "ttl": ttl,
            "comment": comment,
        }
        self._dns_records[record_id] = record
        return record

    async def update_dns_record(
        self,
        zone_id: str,
        record_id: str,
        *,
        record_type: str | None = None,
        name: str | None = None,
        content: str | None = None,
        proxied: bool | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        if record_id not in self._dns_records:
            raise CFAPIError(cf_code=1001, message=f"Record {record_id} not found")
        record = self._dns_records[record_id]
        if record_type is not None:
            record["type"] = record_type
        if name is not None:
            record["name"] = name
        if content is not None:
            record["content"] = content
        if proxied is not None:
            record["proxied"] = proxied
        if comment is not None:
            record["comment"] = comment
        return record

    async def delete_dns_record(self, zone_id: str, record_id: str) -> dict[str, Any]:
        if record_id not in self._dns_records:
            raise CFAPIError(cf_code=1001, message=f"Record {record_id} not found")
        return self._dns_records.pop(record_id)

    # --- Test helpers ---

    def rotate_tunnel_token(self, tunnel_id: str) -> str:
        """Simulate CF rotating a tunnel token."""
        new_token = f"rotated-token-{uuid.uuid4().hex[:8]}"
        self._tunnel_tokens[tunnel_id] = new_token
        return new_token

    def force_tunnel_connections_empty(self, tunnel_id: str) -> None:
        """Simulate all connections dropping."""
        self._tunnel_connections[tunnel_id] = []

    def add_tunnel_connection(self, tunnel_id: str, colo: str = "IAD") -> None:
        """Add a fake healthy connection."""
        conns = self._tunnel_connections.setdefault(tunnel_id, [])
        conns.append(
            {
                "id": str(uuid.uuid4()),
                "colo_name": colo,
                "is_pending_reconnect": False,
                "opened_at": "2024-01-01T00:00:00Z",
            }
        )
