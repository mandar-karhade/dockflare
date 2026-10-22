"""Deployment identity, safe DNS ownership and interrupted-run recovery."""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, patch

import pytest

from app.clients.cloudflare.client import CloudflareClient
from app.deploy_https import new_hostname, provision


@pytest.fixture
def cf():
    client = AsyncMock(spec=CloudflareClient)
    client.get.return_value = {"result": [{"id": "zone", "account": {"id": "account"}}]}
    client.list_tunnels.return_value = []
    client.list_dns_records.return_value = []
    client.create_tunnel.return_value = {"id": "tunnel"}
    client.get_tunnel_token.return_value = "secret-token"
    return client


def test_hostname_uses_target_ipv4_and_four_letters():
    assert re.fullmatch(r"dockflare-152-[a-z]{4}-70\.abbrv8\.com", new_hostname("152.53.135.70"))
    assert re.fullmatch(r"dockflare-192-[a-z]{4}-161\.abbrv8\.com", new_hostname("192.168.0.161"))
    with pytest.raises(ValueError):
        new_hostname("not-an-ip")


@pytest.mark.asyncio
async def test_redeploy_reuses_identity_and_private_token(tmp_path, cf):
    hostname = await provision("152.53.135.70", tmp_path, cf)
    cf.get_tunnel.return_value = {"name": hostname.split(".")[0]}
    cf.list_dns_records.return_value = [
        {"type": "CNAME", "content": "tunnel.cfargotunnel.com", "proxied": True}
    ]
    assert await provision("152.53.135.70", tmp_path, cf) == hostname
    cf.create_tunnel.assert_awaited_once()
    cf.create_dns_record.assert_awaited_once()
    token = tmp_path / ".https-tunnel.env"
    assert token.read_text() == "TUNNEL_TOKEN=secret-token\n"
    assert token.stat().st_mode & 0o777 == 0o600
    assert "secret-token" not in (tmp_path / "deployment-https.json").read_text()
    cf.delete_tunnel.assert_not_awaited()
    cf.delete_dns_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_hostname_collision_generates_another_candidate(tmp_path, cf):
    cf.list_dns_records.side_effect = [[{"type": "A", "content": "other"}], [], []]
    with patch(
        "app.deploy_https.new_hostname",
        side_effect=["dockflare-152-aaaa-70.abbrv8.com", "dockflare-152-bbbb-70.abbrv8.com"],
    ):
        assert await provision("152.53.135.70", tmp_path, cf) == "dockflare-152-bbbb-70.abbrv8.com"
    assert cf.create_tunnel.await_args.args[1] == "dockflare-152-bbbb-70"


@pytest.mark.asyncio
async def test_dns_conflict_never_overwrites_another_service(tmp_path, cf):
    hostname = await provision("152.53.135.70", tmp_path, cf)
    cf.get_tunnel.return_value = {"name": hostname.split(".")[0]}
    cf.list_dns_records.return_value = [{"type": "A", "content": "another-server"}]
    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        await provision("152.53.135.70", tmp_path, cf)
    cf.update_dns_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_interrupted_dns_setup_resumes_same_tunnel(tmp_path, cf):
    cf.create_dns_record.side_effect = RuntimeError("interrupted")
    with pytest.raises(RuntimeError, match="interrupted"):
        await provision("152.53.135.70", tmp_path, cf)
    state = json.loads((tmp_path / "deployment-https.json").read_text())
    cf.get_tunnel.return_value = {"name": state["hostname"].split(".")[0]}
    cf.create_dns_record.side_effect = None
    assert await provision("152.53.135.70", tmp_path, cf) == state["hostname"]
    cf.create_tunnel.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_ip_gets_new_identity_without_cleanup(tmp_path, cf):
    first = await provision("152.53.135.70", tmp_path, cf)
    second = await provision("192.168.0.161", tmp_path, cf)
    assert first != second
    assert second.startswith("dockflare-192-") and second.endswith("-161.abbrv8.com")
    assert cf.create_tunnel.await_count == 2
    cf.delete_tunnel.assert_not_awaited()
    cf.delete_dns_record.assert_not_awaited()
