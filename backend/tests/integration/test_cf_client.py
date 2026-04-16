"""Integration tests for CloudflareClient using FakeCloudflareClient."""

from __future__ import annotations

import pytest

from app.clients.cloudflare.fake import FakeCloudflareClient
from app.core.errors import CFAPIError


@pytest.fixture
def fake_cf() -> FakeCloudflareClient:
    client = FakeCloudflareClient()
    client.seed_account("acc-1", "Test Account")
    client.seed_zone("zone-1", "example.com", "acc-1")
    client.seed_zone("zone-2", "test.dev", "acc-1")
    return client


@pytest.mark.asyncio
async def test_verify_token(fake_cf: FakeCloudflareClient):
    result = await fake_cf.verify_token()
    assert result["status"] == "active"


@pytest.mark.asyncio
async def test_list_accounts(fake_cf: FakeCloudflareClient):
    accounts = await fake_cf.list_accounts()
    assert len(accounts) == 1
    assert accounts[0]["id"] == "acc-1"
    assert accounts[0]["name"] == "Test Account"


@pytest.mark.asyncio
async def test_list_zones(fake_cf: FakeCloudflareClient):
    zones = await fake_cf.list_zones("acc-1")
    assert len(zones) == 2
    names = {z["name"] for z in zones}
    assert names == {"example.com", "test.dev"}


@pytest.mark.asyncio
async def test_list_zones_wrong_account(fake_cf: FakeCloudflareClient):
    zones = await fake_cf.list_zones("nonexistent")
    assert len(zones) == 0


@pytest.mark.asyncio
async def test_tunnel_lifecycle(fake_cf: FakeCloudflareClient):
    # Create
    tunnel = await fake_cf.create_tunnel("acc-1", "my-tunnel")
    assert tunnel["name"] == "my-tunnel"
    tunnel_id = tunnel["id"]

    # Get token
    token = await fake_cf.get_tunnel_token("acc-1", tunnel_id)
    assert token.startswith("fake-token-")

    # List
    tunnels = await fake_cf.list_tunnels("acc-1")
    assert len(tunnels) == 1
    assert tunnels[0]["id"] == tunnel_id

    # Get
    fetched = await fake_cf.get_tunnel("acc-1", tunnel_id)
    assert fetched["name"] == "my-tunnel"

    # Delete
    await fake_cf.delete_tunnel("acc-1", tunnel_id)
    tunnels = await fake_cf.list_tunnels("acc-1")
    assert len(tunnels) == 0


@pytest.mark.asyncio
async def test_get_nonexistent_tunnel(fake_cf: FakeCloudflareClient):
    with pytest.raises(CFAPIError, match="not found"):
        await fake_cf.get_tunnel("acc-1", "nonexistent")


@pytest.mark.asyncio
async def test_tunnel_config(fake_cf: FakeCloudflareClient):
    tunnel = await fake_cf.create_tunnel("acc-1", "cfg-test")
    tunnel_id = tunnel["id"]

    # Default config has catch-all
    config = await fake_cf.get_tunnel_config("acc-1", tunnel_id)
    ingress = config["config"]["ingress"]
    assert ingress[-1] == {"service": "http_status:404"}

    # Update config
    new_config = {
        "ingress": [
            {"hostname": "app.example.com", "service": "http://web:3000"},
            {"service": "http_status:404"},
        ]
    }
    await fake_cf.update_tunnel_config("acc-1", tunnel_id, new_config)

    updated = await fake_cf.get_tunnel_config("acc-1", tunnel_id)
    assert len(updated["config"]["ingress"]) == 2


@pytest.mark.asyncio
async def test_dns_record_lifecycle(fake_cf: FakeCloudflareClient):
    # Create
    record = await fake_cf.create_dns_record(
        "zone-1",
        record_type="CNAME",
        name="app.example.com",
        content="tunnel-uuid.cfargotunnel.com",
        comment="tunnel-manager:route:1",
    )
    assert record["id"]
    assert record["name"] == "app.example.com"

    # List
    records = await fake_cf.list_dns_records("zone-1", name="app.example.com")
    assert len(records) == 1

    # Update
    updated = await fake_cf.update_dns_record(
        "zone-1",
        record["id"],
        content="new-tunnel.cfargotunnel.com",
    )
    assert updated["content"] == "new-tunnel.cfargotunnel.com"

    # Delete
    await fake_cf.delete_dns_record("zone-1", record["id"])
    records = await fake_cf.list_dns_records("zone-1", name="app.example.com")
    assert len(records) == 0


@pytest.mark.asyncio
async def test_dns_duplicate_create(fake_cf: FakeCloudflareClient):
    await fake_cf.create_dns_record(
        "zone-1",
        record_type="CNAME",
        name="dup.example.com",
        content="content",
    )
    with pytest.raises(CFAPIError) as exc_info:
        await fake_cf.create_dns_record(
            "zone-1",
            record_type="CNAME",
            name="dup.example.com",
            content="other-content",
        )
    assert exc_info.value.cf_code == 81057


@pytest.mark.asyncio
async def test_delete_nonexistent_dns(fake_cf: FakeCloudflareClient):
    with pytest.raises(CFAPIError, match="not found"):
        await fake_cf.delete_dns_record("zone-1", "nonexistent")


@pytest.mark.asyncio
async def test_tunnel_connections(fake_cf: FakeCloudflareClient):
    tunnel = await fake_cf.create_tunnel("acc-1", "conn-test")
    tid = tunnel["id"]

    conns = await fake_cf.get_tunnel_connections("acc-1", tid)
    assert len(conns) == 0

    fake_cf.add_tunnel_connection(tid, colo="IAD")
    fake_cf.add_tunnel_connection(tid, colo="SFO")

    conns = await fake_cf.get_tunnel_connections("acc-1", tid)
    assert len(conns) == 2
    colos = {c["colo_name"] for c in conns}
    assert colos == {"IAD", "SFO"}


@pytest.mark.asyncio
async def test_rotate_tunnel_token(fake_cf: FakeCloudflareClient):
    tunnel = await fake_cf.create_tunnel("acc-1", "rotate-test")
    tid = tunnel["id"]

    old_token = await fake_cf.get_tunnel_token("acc-1", tid)
    new_token = fake_cf.rotate_tunnel_token(tid)
    fetched = await fake_cf.get_tunnel_token("acc-1", tid)

    assert fetched == new_token
    assert fetched != old_token


@pytest.mark.asyncio
async def test_seeded_dns_records(fake_cf: FakeCloudflareClient):
    """Seed a DNS record before testing conflict detection."""
    fake_cf.seed_dns_record(
        zone_id="zone-1",
        name="external.example.com",
        record_type="A",
        content="192.0.2.1",
    )
    records = await fake_cf.list_dns_records("zone-1", name="external.example.com")
    assert len(records) == 1
    assert records[0]["type"] == "A"
    assert records[0]["content"] == "192.0.2.1"
