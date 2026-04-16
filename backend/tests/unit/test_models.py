"""Unit tests for SQLModel classes and DB constraints."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppSetting,
    AuditLog,
    CfCredential,
    ContainerCache,
    Route,
    Tunnel,
    ZoneCache,
)
from app.utils.time import utcnow


@pytest.mark.asyncio
async def test_create_credential(db_session: AsyncSession, vault):
    token = "test-token-1234"
    cred = CfCredential(
        name="Test Token",
        account_id="acc-1",
        account_name="Test Account",
        token_encrypted=vault.encrypt(token),
        token_last_four=token[-4:],
        token_fingerprint="fp-unique-1",
    )
    db_session.add(cred)
    await db_session.commit()
    await db_session.refresh(cred)

    assert cred.id is not None
    assert cred.is_active is True
    assert cred.token_last_four == "1234"
    assert vault.decrypt(cred.token_encrypted) == token


@pytest.mark.asyncio
async def test_credential_fingerprint_unique(db_session: AsyncSession, vault):
    base = dict(
        name="t",
        account_id="a",
        token_encrypted=vault.encrypt("x"),
        token_last_four="xxxx",
        token_fingerprint="same-fp",
    )
    db_session.add(CfCredential(**base))
    await db_session.commit()

    db_session.add(CfCredential(**base))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_create_tunnel(db_session: AsyncSession, vault):
    # Need a credential first
    cred = CfCredential(
        name="Test",
        account_id="acc-1",
        token_encrypted=vault.encrypt("cred-token"),
        token_last_four="oken",
        token_fingerprint="fp-1",
    )
    db_session.add(cred)
    await db_session.commit()
    await db_session.refresh(cred)

    tunnel = Tunnel(
        cf_tunnel_id="cf-uuid-1",
        cf_tunnel_name="test-tunnel",
        cf_credential_id=cred.id,
        account_id="acc-1",
        token_encrypted=vault.encrypt("tunnel-token"),
        token_last_four="oken",
        token_fetched_at=utcnow(),
    )
    db_session.add(tunnel)
    await db_session.commit()
    await db_session.refresh(tunnel)

    assert tunnel.id is not None
    assert tunnel.status == "active"
    assert tunnel.rotation_policy == "manual"


@pytest.mark.asyncio
async def test_tunnel_cf_id_unique(db_session: AsyncSession, vault):
    cred = CfCredential(
        name="T",
        account_id="a",
        token_encrypted=vault.encrypt("t"),
        token_last_four="tttt",
        token_fingerprint="fp-uniq",
    )
    db_session.add(cred)
    await db_session.commit()
    await db_session.refresh(cred)

    base = dict(
        cf_tunnel_id="same-uuid",
        cf_tunnel_name="t",
        cf_credential_id=cred.id,
        account_id="a",
        token_encrypted=vault.encrypt("t"),
        token_last_four="tttt",
        token_fetched_at=utcnow(),
    )
    db_session.add(Tunnel(**base))
    await db_session.commit()

    db_session.add(Tunnel(**base))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_create_route(db_session: AsyncSession, vault):
    cred = CfCredential(
        name="T",
        account_id="a",
        token_encrypted=vault.encrypt("t"),
        token_last_four="tttt",
        token_fingerprint="fp-route-test",
    )
    db_session.add(cred)
    await db_session.commit()
    await db_session.refresh(cred)

    tunnel = Tunnel(
        cf_tunnel_id="cf-uuid-route",
        cf_tunnel_name="route-tunnel",
        cf_credential_id=cred.id,
        account_id="a",
        token_encrypted=vault.encrypt("t"),
        token_last_four="tttt",
        token_fetched_at=utcnow(),
    )
    db_session.add(tunnel)
    await db_session.commit()
    await db_session.refresh(tunnel)

    route = Route(
        tunnel_id=tunnel.id,
        hostname="app.example.com",
        priority=100,
        target_compose_project="myapp",
        target_compose_service="web",
        target_port=3000,
        zone_id="zone-1",
        zone_name="example.com",
    )
    db_session.add(route)
    await db_session.commit()
    await db_session.refresh(route)

    assert route.id is not None
    assert route.status == "provisioning"
    assert route.target_scheme == "http"
    assert route.dns_proxied is True


@pytest.mark.asyncio
async def test_cascade_delete_tunnel_removes_routes(db_session: AsyncSession, vault):
    """Routes have ON DELETE CASCADE for tunnel_id."""
    cred = CfCredential(
        name="T",
        account_id="a",
        token_encrypted=vault.encrypt("t"),
        token_last_four="tttt",
        token_fingerprint="fp-cascade",
    )
    db_session.add(cred)
    await db_session.commit()
    await db_session.refresh(cred)

    tunnel = Tunnel(
        cf_tunnel_id="cf-cascade",
        cf_tunnel_name="cascade",
        cf_credential_id=cred.id,
        account_id="a",
        token_encrypted=vault.encrypt("t"),
        token_last_four="tttt",
        token_fetched_at=utcnow(),
    )
    db_session.add(tunnel)
    await db_session.commit()
    await db_session.refresh(tunnel)

    route = Route(
        tunnel_id=tunnel.id,
        hostname="cascade.example.com",
        priority=100,
        zone_id="z",
        zone_name="example.com",
    )
    db_session.add(route)
    await db_session.commit()

    # Delete the tunnel
    await db_session.delete(tunnel)
    await db_session.commit()

    # Route should be gone
    result = await db_session.execute(select(Route).where(Route.hostname == "cascade.example.com"))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_audit_log(db_session: AsyncSession):
    log = AuditLog(
        actor="user:1",
        action="tunnel.create",
        entity_type="tunnel",
        entity_id=1,
        status="success",
    )
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)

    assert log.id is not None
    assert log.created_at is not None


@pytest.mark.asyncio
async def test_app_settings(db_session: AsyncSession):
    setting = AppSetting(key="bootstrap.completed", value="true")
    db_session.add(setting)
    await db_session.commit()

    result = await db_session.execute(
        select(AppSetting).where(AppSetting.key == "bootstrap.completed")
    )
    fetched = result.scalar_one()
    assert fetched.value == "true"


@pytest.mark.asyncio
async def test_zone_cache(db_session: AsyncSession):
    zone = ZoneCache(
        zone_id="z-1",
        zone_name="example.com",
        account_id="acc-1",
        plan_name="free",
        status="active",
    )
    db_session.add(zone)
    await db_session.commit()
    await db_session.refresh(zone)
    assert zone.zone_id == "z-1"


@pytest.mark.asyncio
async def test_container_cache(db_session: AsyncSession):
    container = ContainerCache(
        container_id="abc123",
        container_name="myapp_web_1",
        compose_project="myapp",
        compose_service="web",
        status="running",
    )
    db_session.add(container)
    await db_session.commit()
    await db_session.refresh(container)
    assert container.managed_by_tm is False
