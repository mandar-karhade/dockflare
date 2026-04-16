"""Integration tests for RouteService with fake clients."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.cloudflare.fake import FakeCloudflareClient
from app.clients.docker.fake import FakeDockerClient
from app.core.errors import NotFoundError
from app.core.vault import VaultService
from app.models.credential import CfCredential
from app.services.route_service import RouteService
from app.services.tunnel_service import TunnelService


async def _setup(
    db: AsyncSession,
    vault: VaultService,
    fake_cf: FakeCloudflareClient,
    fake_docker: FakeDockerClient,
) -> tuple[TunnelService, RouteService, int]:
    """Seed a credential and create a tunnel, return services + tunnel_id."""
    cred = CfCredential(
        name="T",
        account_id="acc-1",
        token_encrypted=vault.encrypt("t"),
        token_last_four="tttt",
        token_fingerprint="fp-route-svc",
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)

    tunnel_svc = TunnelService(
        db=db,
        vault=vault,
        cf_client=fake_cf,
        docker_client=fake_docker,
        credential_id=cred.id,
        account_id="acc-1",
    )
    route_svc = RouteService(
        db=db,
        vault=vault,
        cf_client=fake_cf,
        docker_client=fake_docker,
        account_id="acc-1",
    )

    tunnel = await tunnel_svc.create(name="test-tunnel")
    return tunnel_svc, route_svc, tunnel.id


@pytest.fixture
def fake_cf() -> FakeCloudflareClient:
    client = FakeCloudflareClient()
    client.seed_account("acc-1", "Test Account")
    client.seed_zone("zone-1", "example.com", "acc-1")
    client.seed_zone("zone-2", "test.dev", "acc-1")
    return client


@pytest.fixture
def fake_docker() -> FakeDockerClient:
    return FakeDockerClient()


@pytest.mark.asyncio
async def test_create_route_happy_path(
    db_session: AsyncSession,
    vault: VaultService,
    fake_cf: FakeCloudflareClient,
    fake_docker: FakeDockerClient,
):
    _, route_svc, tunnel_id = await _setup(db_session, vault, fake_cf, fake_docker)

    route = await route_svc.create(
        tunnel_id=tunnel_id,
        hostname="app.example.com",
        target_compose_project="myapp",
        target_compose_service="web",
        target_port=3000,
    )

    assert route.status == "active"
    assert route.cf_dns_record_id is not None
    assert route.zone_id == "zone-1"
    assert route.zone_name == "example.com"

    # DNS record exists in CF
    records = await fake_cf.list_dns_records("zone-1", name="app.example.com")
    assert len(records) == 1
    assert records[0]["type"] == "CNAME"
    assert records[0]["proxied"] is True
    assert records[0]["comment"].startswith("tunnel-manager:")

    # Ingress updated with route + catch-all
    from sqlalchemy import select

    from app.models.tunnel import Tunnel

    tunnel_result = await db_session.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
    tunnel = tunnel_result.scalar_one()
    cf_config = await fake_cf.get_tunnel_config("acc-1", tunnel.cf_tunnel_id)
    ingress = cf_config["config"]["ingress"]
    assert any(r.get("hostname") == "app.example.com" for r in ingress)
    assert ingress[-1] == {"service": "http_status:404"}


@pytest.mark.asyncio
async def test_delete_route(
    db_session: AsyncSession,
    vault: VaultService,
    fake_cf: FakeCloudflareClient,
    fake_docker: FakeDockerClient,
):
    _, route_svc, tunnel_id = await _setup(db_session, vault, fake_cf, fake_docker)

    route = await route_svc.create(
        tunnel_id=tunnel_id,
        hostname="del.example.com",
        target_compose_service="web",
        target_port=3000,
    )

    await route_svc.delete(route.id)

    # DNS record gone
    records = await fake_cf.list_dns_records("zone-1", name="del.example.com")
    assert len(records) == 0

    # DB row gone
    with pytest.raises(NotFoundError):
        await route_svc.get(route.id)


@pytest.mark.asyncio
async def test_create_route_wrong_zone(
    db_session: AsyncSession,
    vault: VaultService,
    fake_cf: FakeCloudflareClient,
    fake_docker: FakeDockerClient,
):
    _, route_svc, tunnel_id = await _setup(db_session, vault, fake_cf, fake_docker)

    with pytest.raises(NotFoundError, match="No zone matches"):
        await route_svc.create(
            tunnel_id=tunnel_id,
            hostname="app.unknown-domain.org",
            target_compose_service="web",
            target_port=3000,
        )


@pytest.mark.asyncio
async def test_multiple_routes_same_tunnel(
    db_session: AsyncSession,
    vault: VaultService,
    fake_cf: FakeCloudflareClient,
    fake_docker: FakeDockerClient,
):
    _, route_svc, tunnel_id = await _setup(db_session, vault, fake_cf, fake_docker)

    await route_svc.create(
        tunnel_id=tunnel_id,
        hostname="a.example.com",
        target_compose_service="web",
        target_port=3000,
    )
    await route_svc.create(
        tunnel_id=tunnel_id,
        hostname="b.example.com",
        target_compose_service="api",
        target_port=8080,
    )

    routes = await route_svc.list_all(tunnel_id=tunnel_id)
    assert len(routes) == 2
