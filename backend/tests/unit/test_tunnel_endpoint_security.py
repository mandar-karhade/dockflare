"""Security-focused tests for tunnel API endpoints."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.deps as deps
import app.api.v1.tunnels as tunnels_api
from app.config import Settings
from app.main import create_app


class FakeCloudflareClient:
    def __init__(self) -> None:
        self.updated_configs: list[dict[str, Any]] = []

    async def list_accounts(self) -> list[dict[str, str]]:
        return [{"id": "acc-1"}]

    async def create_tunnel(self, account_id: str, name: str) -> dict[str, str]:
        return {"id": "tun-1", "name": name}

    async def get_tunnel_token(self, account_id: str, tunnel_id: str) -> str:
        return "token"

    async def get_tunnel_config(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        return {"config": {"ingress": [{"service": "http_status:404"}]}}

    async def update_tunnel_config(
        self, account_id: str, tunnel_id: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        self.updated_configs.append(config)
        return {"config": config}

    async def list_zones(self, account_id: str) -> list[dict[str, str]]:
        return [{"id": "zone-1", "name": "example.com"}]

    async def list_dns_records(
        self, zone_id: str, name: str | None = None, record_type: str | None = None
    ) -> list[dict[str, Any]]:
        return []

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
        return {"id": "dns-1", "name": name, "content": content}


class FakeDockerClient:
    def __init__(self) -> None:
        self.images: list[str] = []

    async def spawn_cloudflared(
        self,
        *,
        name: str,
        image: str,
        token: str,
        networks: list[str],
        labels: dict[str, str],
    ) -> dict[str, str]:
        self.images.append(image)
        return {"Id": "abcdef1234567890"}


class FakeTunnelCache:
    async def refresh_tunnel(
        self, client: FakeCloudflareClient, account_id: str, tunnel_id: str
    ) -> dict[str, Any]:
        return {}


@pytest.mark.asyncio
async def test_create_tunnel_uses_configured_cloudflared_image(monkeypatch) -> None:
    fake_docker = FakeDockerClient()
    monkeypatch.setattr(deps, "get_cf_client", lambda: FakeCloudflareClient())
    monkeypatch.setattr(deps, "get_docker_client", lambda: fake_docker)
    monkeypatch.setattr(deps, "get_cf_tunnel_cache", lambda: FakeTunnelCache())
    monkeypatch.setattr(tunnels_api, "get_cf_client", lambda: FakeCloudflareClient())
    monkeypatch.setattr(tunnels_api, "get_cf_tunnel_cache", lambda: FakeTunnelCache())

    settings = Settings(
        _env_file=None,
        DOCKFLARE_BASIC_AUTH_USER="",
        DOCKFLARE_BASIC_AUTH_PASSWORD="",
        TM_CLOUDFLARED_IMAGE="cloudflare/cloudflared:2024.10.0",
    )
    transport = ASGITransport(app=create_app(settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/tunnels", json={"name": "demo"})

    assert response.status_code == 200
    assert fake_docker.images == ["cloudflare/cloudflared:2024.10.0"]


@pytest.mark.asyncio
async def test_update_ingress_normalizes_url_hostname(monkeypatch) -> None:
    fake_cf = FakeCloudflareClient()
    monkeypatch.setattr(deps, "get_cf_client", lambda: fake_cf)
    monkeypatch.setattr(deps, "get_cf_tunnel_cache", lambda: FakeTunnelCache())
    monkeypatch.setattr(tunnels_api, "get_cf_client", lambda: fake_cf)
    monkeypatch.setattr(tunnels_api, "get_cf_tunnel_cache", lambda: FakeTunnelCache())

    transport = ASGITransport(app=create_app(Settings(_env_file=None)))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/v1/tunnels/tun-1/ingress",
            json=[{"hostname": "https://app.example.com", "service": "http://web:3000"}],
        )

    assert response.status_code == 200
    assert fake_cf.updated_configs[0]["ingress"][0]["hostname"] == "app.example.com"
