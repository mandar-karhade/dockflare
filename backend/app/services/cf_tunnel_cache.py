"""In-memory cache for Cloudflare tunnel dashboard data."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CachedTunnelData:
    tunnel: dict[str, Any]
    connections: list[dict[str, Any]]
    config: dict[str, Any]


class CloudflareTunnelCache:
    """Caches CF tunnel list, connection, and config data until explicitly refreshed."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._default_account_id: str | None = None
        self._tunnels_by_account: dict[str, list[dict[str, Any]]] = {}
        self._details_by_account: dict[str, dict[str, CachedTunnelData]] = {}

    async def get_default_account_id(self, client: Any, *, force: bool = False) -> str | None:
        async with self._lock:
            if self._default_account_id is not None and not force:
                return self._default_account_id

            accounts = await client.list_accounts()
            self._default_account_id = accounts[0]["id"] if accounts else None
            return self._default_account_id

    async def get_all(self, client: Any, account_id: str) -> list[CachedTunnelData]:
        async with self._lock:
            if account_id not in self._tunnels_by_account:
                return await self._refresh_all_unlocked(client, account_id)

            details = self._details_by_account.setdefault(account_id, {})
            for tunnel in self._tunnels_by_account[account_id]:
                tunnel_id = tunnel["id"]
                if tunnel_id not in details:
                    details[tunnel_id] = await self._fetch_detail(client, account_id, tunnel)

            return self._clone_items(
                details[tunnel["id"]] for tunnel in self._tunnels_by_account[account_id]
            )

    async def refresh_all(self, client: Any, account_id: str) -> list[CachedTunnelData]:
        async with self._lock:
            return await self._refresh_all_unlocked(client, account_id)

    async def refresh_tunnel(
        self, client: Any, account_id: str, tunnel_id: str
    ) -> CachedTunnelData:
        async with self._lock:
            tunnel = await client.get_tunnel(account_id, tunnel_id)
            detail = await self._fetch_detail(client, account_id, tunnel)
            self._details_by_account.setdefault(account_id, {})[tunnel_id] = detail

            tunnels = self._tunnels_by_account.setdefault(account_id, [])
            for index, existing in enumerate(tunnels):
                if existing["id"] == tunnel_id:
                    tunnels[index] = deepcopy(tunnel)
                    break
            else:
                tunnels.append(deepcopy(tunnel))

            return self._clone_item(detail)

    def invalidate_all(self, account_id: str | None = None) -> None:
        if account_id is None:
            self._default_account_id = None
            self._tunnels_by_account.clear()
            self._details_by_account.clear()
            return
        self._tunnels_by_account.pop(account_id, None)
        self._details_by_account.pop(account_id, None)

    def invalidate_tunnel(self, account_id: str, tunnel_id: str) -> None:
        self._details_by_account.get(account_id, {}).pop(tunnel_id, None)
        if account_id in self._tunnels_by_account:
            self._tunnels_by_account[account_id] = [
                tunnel
                for tunnel in self._tunnels_by_account[account_id]
                if tunnel["id"] != tunnel_id
            ]

    async def _refresh_all_unlocked(self, client: Any, account_id: str) -> list[CachedTunnelData]:
        tunnels = await client.list_tunnels(account_id)
        details: dict[str, CachedTunnelData] = {}
        for tunnel in tunnels:
            details[tunnel["id"]] = await self._fetch_detail(client, account_id, tunnel)

        self._tunnels_by_account[account_id] = deepcopy(tunnels)
        self._details_by_account[account_id] = details
        return self._clone_items(details[tunnel["id"]] for tunnel in tunnels)

    async def _fetch_detail(
        self, client: Any, account_id: str, tunnel: dict[str, Any]
    ) -> CachedTunnelData:
        tunnel_id = tunnel["id"]
        connections = await client.get_tunnel_connections(account_id, tunnel_id)
        try:
            config = await client.get_tunnel_config(account_id, tunnel_id)
        except Exception:
            config = {}
        if not isinstance(config, dict):
            config = {}
        if not isinstance(config.get("config"), dict):
            config["config"] = {}
        return CachedTunnelData(
            tunnel=deepcopy(tunnel),
            connections=deepcopy(connections),
            config=deepcopy(config),
        )

    def _clone_item(self, item: CachedTunnelData) -> CachedTunnelData:
        return CachedTunnelData(
            tunnel=deepcopy(item.tunnel),
            connections=deepcopy(item.connections),
            config=deepcopy(item.config),
        )

    def _clone_items(self, items: Any) -> list[CachedTunnelData]:
        return [self._clone_item(item) for item in items]
