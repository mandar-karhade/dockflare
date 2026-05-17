"""Integration tests for DockerClient using FakeDockerClient."""

from __future__ import annotations

import pytest

from app.clients.docker.fake import FakeDockerClient
from app.clients.docker.helpers import (
    get_compose_identity,
    get_container_name,
    get_exposed_ports,
    get_networks,
    is_managed,
    sidecar_name,
)
from app.core.errors import NotFoundError


@pytest.fixture
def fake_docker() -> FakeDockerClient:
    client = FakeDockerClient()
    client.seed_container(
        id="abc123",
        name="myapp_web_1",
        labels={
            "com.docker.compose.project": "myapp",
            "com.docker.compose.service": "web",
        },
        exposed_ports=[3000],
        networks=["myapp_default"],
        status="running",
    )
    client.seed_container(
        id="def456",
        name="myapp_db_1",
        labels={
            "com.docker.compose.project": "myapp",
            "com.docker.compose.service": "db",
        },
        exposed_ports=[5432],
        networks=["myapp_default"],
        status="running",
    )
    return client


@pytest.mark.asyncio
async def test_list_all_containers(fake_docker: FakeDockerClient):
    containers = await fake_docker.list_containers()
    assert len(containers) == 2


@pytest.mark.asyncio
async def test_get_container(fake_docker: FakeDockerClient):
    attrs = await fake_docker.get_container("abc123")
    assert attrs["Id"] == "abc123"
    assert attrs["State"]["Running"] is True


@pytest.mark.asyncio
async def test_get_nonexistent_container(fake_docker: FakeDockerClient):
    with pytest.raises(NotFoundError):
        await fake_docker.get_container("nonexistent")


@pytest.mark.asyncio
async def test_find_by_compose(fake_docker: FakeDockerClient):
    results = await fake_docker.find_by_compose("myapp")
    assert len(results) == 2

    results = await fake_docker.find_by_compose("myapp", "web")
    assert len(results) == 1
    assert results[0]["Id"] == "abc123"


@pytest.mark.asyncio
async def test_spawn_cloudflared(fake_docker: FakeDockerClient):
    attrs = await fake_docker.spawn_cloudflared(
        name="cftunnel-myapp-web",
        image="cloudflare/cloudflared:2024.10.0",
        token="test-tunnel-token",
        networks=["myapp_default"],
        labels={
            "tunnel-manager.tunnel.id": "1",
            "tunnel-manager.tunnel.cf-id": "cf-uuid",
        },
    )
    assert attrs["State"]["Running"] is True
    assert attrs["Config"]["Labels"]["tunnel-manager.managed"] == "true"
    assert attrs["Config"]["Labels"]["tunnel-manager.tunnel.id"] == "1"
    # Token in env for assertions
    assert any("TUNNEL_TOKEN=test-tunnel-token" in e for e in attrs["Config"]["Env"])


@pytest.mark.asyncio
async def test_list_managed_sidecars(fake_docker: FakeDockerClient):
    # No managed sidecars yet
    sidecars = await fake_docker.list_managed_sidecars()
    assert len(sidecars) == 0

    # Spawn one
    await fake_docker.spawn_cloudflared(
        name="cftunnel-test",
        image="cloudflare/cloudflared:latest",
        token="tok",
        networks=["test_default"],
        labels={"tunnel-manager.tunnel.id": "1"},
    )
    sidecars = await fake_docker.list_managed_sidecars()
    assert len(sidecars) == 1


@pytest.mark.asyncio
async def test_stop_and_remove(fake_docker: FakeDockerClient):
    attrs = await fake_docker.spawn_cloudflared(
        name="cftunnel-rm",
        image="cloudflare/cloudflared:latest",
        token="tok",
        networks=["net"],
        labels={},
    )
    cid = attrs["Id"]

    await fake_docker.stop_container(cid)
    stopped = await fake_docker.get_container(cid)
    assert stopped["State"]["Running"] is False

    await fake_docker.remove_container(cid)
    with pytest.raises(NotFoundError):
        await fake_docker.get_container(cid)


@pytest.mark.asyncio
async def test_network_operations(fake_docker: FakeDockerClient):
    networks = await fake_docker.get_container_networks("abc123")
    assert "myapp_default" in networks

    await fake_docker.connect_to_network("abc123", "other_network")
    networks = await fake_docker.get_container_networks("abc123")
    assert "other_network" in networks


# --- Helper function tests ---


def test_get_compose_identity():
    attrs = {
        "Config": {
            "Labels": {
                "com.docker.compose.project": "myapp",
                "com.docker.compose.service": "web",
            }
        }
    }
    project, service = get_compose_identity(attrs)
    assert project == "myapp"
    assert service == "web"


def test_get_container_name():
    assert get_container_name({"Name": "/myapp_web_1"}) == "myapp_web_1"
    assert get_container_name({"Name": "myapp_web_1"}) == "myapp_web_1"


def test_get_networks():
    attrs = {"NetworkSettings": {"Networks": {"myapp_default": {}, "bridge": {}}}}
    nets = get_networks(attrs)
    assert set(nets) == {"myapp_default", "bridge"}


def test_get_exposed_ports():
    attrs = {"Config": {"ExposedPorts": {"3000/tcp": {}, "8080/tcp": {}, "53/udp": {}, "bad-key": {}}}}
    ports = get_exposed_ports(attrs)
    assert set(ports) == {3000, 8080}  # UDP filtered out


def test_is_managed():
    assert is_managed({"Config": {"Labels": {"tunnel-manager.managed": "true"}}})
    assert not is_managed({"Config": {"Labels": {}}})


def test_sidecar_name():
    assert sidecar_name("ghost", "web") == "cftunnel-ghost-web"
    assert sidecar_name("ghost", "web", "new") == "cftunnel-ghost-web-new"
