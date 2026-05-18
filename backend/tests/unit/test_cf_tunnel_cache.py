from __future__ import annotations

from typing import Any

import pytest

from app.clients.cloudflare.fake import FakeCloudflareClient
from app.services.cf_tunnel_cache import CloudflareTunnelCache


class CountingCloudflareClient(FakeCloudflareClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls: dict[str, int] = {
            "list_accounts": 0,
            "list_tunnels": 0,
            "get_tunnel": 0,
            "get_tunnel_connections": 0,
            "get_tunnel_config": 0,
        }

    async def list_accounts(self) -> list[dict[str, Any]]:
        self.calls["list_accounts"] += 1
        return await super().list_accounts()

    async def list_tunnels(self, account_id: str) -> list[dict[str, Any]]:
        self.calls["list_tunnels"] += 1
        return await super().list_tunnels(account_id)

    async def get_tunnel(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        self.calls["get_tunnel"] += 1
        return await super().get_tunnel(account_id, tunnel_id)

    async def get_tunnel_connections(self, account_id: str, tunnel_id: str) -> list[dict[str, Any]]:
        self.calls["get_tunnel_connections"] += 1
        return await super().get_tunnel_connections(account_id, tunnel_id)

    async def get_tunnel_config(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        self.calls["get_tunnel_config"] += 1
        return await super().get_tunnel_config(account_id, tunnel_id)


class EmptyConfigCloudflareClient(CountingCloudflareClient):
    async def get_tunnel_config(self, account_id: str, tunnel_id: str) -> dict[str, Any] | None:
        self.calls["get_tunnel_config"] += 1
        return {"config": None}


@pytest.mark.asyncio
async def test_get_all_uses_cached_cloudflare_tunnel_details_until_forced() -> None:
    client = CountingCloudflareClient()
    client.seed_account("acc-1", "Account")
    first = await client.create_tunnel("acc-1", "first")
    second = await client.create_tunnel("acc-1", "second")
    cache = CloudflareTunnelCache()

    first_result = await cache.get_all(client, "acc-1")
    second_result = await cache.get_all(client, "acc-1")

    assert [item.tunnel["id"] for item in first_result] == [first["id"], second["id"]]
    assert [item.tunnel["id"] for item in second_result] == [first["id"], second["id"]]
    assert client.calls["list_tunnels"] == 1
    assert client.calls["get_tunnel_connections"] == 2
    assert client.calls["get_tunnel_config"] == 2

    await cache.refresh_all(client, "acc-1")

    assert client.calls["list_tunnels"] == 2
    assert client.calls["get_tunnel_connections"] == 4
    assert client.calls["get_tunnel_config"] == 4


@pytest.mark.asyncio
async def test_get_default_account_id_is_cached_until_forced() -> None:
    client = CountingCloudflareClient()
    client.seed_account("acc-1", "Account")
    cache = CloudflareTunnelCache()

    assert await cache.get_default_account_id(client) == "acc-1"
    assert await cache.get_default_account_id(client) == "acc-1"
    assert client.calls["list_accounts"] == 1

    assert await cache.get_default_account_id(client, force=True) == "acc-1"
    assert client.calls["list_accounts"] == 2


@pytest.mark.asyncio
async def test_refresh_tunnel_updates_only_one_cached_tunnel() -> None:
    client = CountingCloudflareClient()
    client.seed_account("acc-1", "Account")
    first = await client.create_tunnel("acc-1", "first")
    await client.create_tunnel("acc-1", "second")
    cache = CloudflareTunnelCache()
    await cache.get_all(client, "acc-1")

    await cache.refresh_tunnel(client, "acc-1", first["id"])

    assert client.calls["list_tunnels"] == 1
    assert client.calls["get_tunnel"] == 1
    assert client.calls["get_tunnel_connections"] == 3
    assert client.calls["get_tunnel_config"] == 3


@pytest.mark.asyncio
async def test_empty_tunnel_config_is_normalized() -> None:
    client = EmptyConfigCloudflareClient()
    client.seed_account("acc-1", "Account")
    await client.create_tunnel("acc-1", "first")
    cache = CloudflareTunnelCache()

    result = await cache.get_all(client, "acc-1")

    assert result[0].config["config"].get("ingress", []) == []
