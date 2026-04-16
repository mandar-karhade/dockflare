"""Integration tests for TunnelService with fake clients."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.cloudflare.fake import FakeCloudflareClient
from app.clients.docker.fake import FakeDockerClient
from app.core.vault import VaultService
from app.models.credential import CfCredential
from app.models.tunnel import Tunnel
from app.services.tunnel_service import TunnelService


async def _seed_credential(db: AsyncSession, vault: VaultService) -> CfCredential:
    cred = CfCredential(
        name="Test",
        account_id="acc-1",
        token_encrypted=vault.encrypt("cf-api-token"),
        token_last_four="oken",
        token_fingerprint="fp-svc-test",
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return cred


@pytest.fixture
def fake_cf() -> FakeCloudflareClient:
    client = FakeCloudflareClient()
    client.seed_account("acc-1", "Test Account")
    client.seed_zone("zone-1", "example.com", "acc-1")
    return client


@pytest.fixture
def fake_docker() -> FakeDockerClient:
    client = FakeDockerClient()
    client.seed_container(
        id="target-abc",
        name="myapp_web_1",
        labels={
            "com.docker.compose.project": "myapp",
            "com.docker.compose.service": "web",
        },
        exposed_ports=[3000],
        networks=["myapp_default"],
    )
    return client


@pytest.mark.asyncio
async def test_create_tunnel_happy_path(
    db_session: AsyncSession,
    vault: VaultService,
    fake_cf: FakeCloudflareClient,
    fake_docker: FakeDockerClient,
):
    cred = await _seed_credential(db_session, vault)
    svc = TunnelService(
        db=db_session,
        vault=vault,
        cf_client=fake_cf,
        docker_client=fake_docker,
        credential_id=cred.id,
        account_id="acc-1",
    )

    tunnel = await svc.create(
        name="myapp-tunnel",
        primary_compose_project="myapp",
        primary_compose_service="web",
    )

    assert tunnel.id is not None
    assert tunnel.status == "active"
    assert tunnel.cf_tunnel_id is not None
    assert tunnel.cloudflared_container_id is not None

    # CF tunnel exists
    cf_tunnels = await fake_cf.list_tunnels("acc-1")
    assert len(cf_tunnels) == 1

    # Sidecar exists
    sidecars = await fake_docker.list_managed_sidecars()
    assert len(sidecars) == 1
    assert sidecars[0]["Config"]["Labels"]["tunnel-manager.tunnel.id"] == str(tunnel.id)


@pytest.mark.asyncio
async def test_delete_tunnel(
    db_session: AsyncSession,
    vault: VaultService,
    fake_cf: FakeCloudflareClient,
    fake_docker: FakeDockerClient,
):
    cred = await _seed_credential(db_session, vault)
    svc = TunnelService(
        db=db_session,
        vault=vault,
        cf_client=fake_cf,
        docker_client=fake_docker,
        credential_id=cred.id,
        account_id="acc-1",
    )

    tunnel = await svc.create(name="to-delete")
    tunnel_id = tunnel.id

    await svc.delete(tunnel_id)

    # DB row gone
    result = await db_session.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
    assert result.scalar_one_or_none() is None

    # CF tunnel gone
    cf_tunnels = await fake_cf.list_tunnels("acc-1")
    assert len(cf_tunnels) == 0

    # Sidecar gone
    sidecars = await fake_docker.list_managed_sidecars()
    assert len(sidecars) == 0


@pytest.mark.asyncio
async def test_list_tunnels(
    db_session: AsyncSession,
    vault: VaultService,
    fake_cf: FakeCloudflareClient,
    fake_docker: FakeDockerClient,
):
    cred = await _seed_credential(db_session, vault)
    svc = TunnelService(
        db=db_session,
        vault=vault,
        cf_client=fake_cf,
        docker_client=fake_docker,
        credential_id=cred.id,
        account_id="acc-1",
    )

    await svc.create(name="tunnel-1")
    await svc.create(name="tunnel-2")

    tunnels = await svc.list_all()
    assert len(tunnels) == 2
