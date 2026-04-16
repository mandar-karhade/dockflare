"""Fake DockerClient for testing — in-memory container state."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.errors import NotFoundError


class FakeDockerClient:
    """In-memory fake implementing the same interface as DockerClient."""

    def __init__(self) -> None:
        self._containers: dict[str, dict[str, Any]] = {}
        self._networks: dict[str, set[str]] = {}  # network_name -> set of container_ids

    # --- Seeding ---

    def seed_container(
        self,
        *,
        id: str,
        name: str,
        labels: dict[str, str] | None = None,
        exposed_ports: list[int] | None = None,
        networks: list[str] | None = None,
        status: str = "running",
        image: str = "nginx:latest",
    ) -> dict[str, Any]:
        container_networks = {}
        for net in networks or []:
            container_networks[net] = {"NetworkID": f"net-{net}"}
            self._networks.setdefault(net, set()).add(id)

        attrs: dict[str, Any] = {
            "Id": id,
            "Name": f"/{name}",
            "State": {"Status": status, "Running": status == "running"},
            "Config": {
                "Image": image,
                "Labels": labels or {},
                "ExposedPorts": {f"{p}/tcp": {} for p in (exposed_ports or [])},
            },
            "NetworkSettings": {"Networks": container_networks},
        }
        self._containers[id] = attrs
        return attrs

    # --- Container ops ---

    async def close(self) -> None:
        pass

    async def ping(self) -> bool:
        return True

    async def list_containers(
        self, *, all: bool = True, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        results = list(self._containers.values())
        if not all:
            results = [c for c in results if c["State"]["Running"]]
        if filters:
            label_filters = filters.get("label", [])
            for lf in label_filters:
                if "=" in lf:
                    key, val = lf.split("=", 1)
                    results = [c for c in results if c["Config"]["Labels"].get(key) == val]
                else:
                    results = [c for c in results if lf in c["Config"]["Labels"]]
        return results

    async def get_container(self, container_id: str) -> dict[str, Any]:
        if container_id not in self._containers:
            raise NotFoundError("container", container_id)
        return self._containers[container_id]

    async def find_by_compose(
        self, project: str, service: str | None = None
    ) -> list[dict[str, Any]]:
        filters: dict[str, list[str]] = {"label": [f"com.docker.compose.project={project}"]}
        if service:
            filters["label"].append(f"com.docker.compose.service={service}")
        return await self.list_containers(filters=filters)

    async def find_by_label(self, label: str, value: str) -> list[dict[str, Any]]:
        return await self.list_containers(filters={"label": [f"{label}={value}"]})

    async def list_managed_sidecars(self) -> list[dict[str, Any]]:
        return await self.list_containers(filters={"label": ["tunnel-manager.managed=true"]})

    # --- Spawn ---

    async def spawn_cloudflared(
        self,
        *,
        name: str,
        image: str,
        token: str,
        networks: list[str],
        labels: dict[str, str],
        mem_limit: str = "128m",
    ) -> dict[str, Any]:
        container_id = str(uuid.uuid4())[:12]
        merged_labels = {**labels, "tunnel-manager.managed": "true"}
        attrs = self.seed_container(
            id=container_id,
            name=name,
            labels=merged_labels,
            networks=networks,
            status="running",
            image=image,
        )
        # Store token in env for test assertions
        attrs["Config"]["Env"] = [f"TUNNEL_TOKEN={token}"]
        return attrs

    # --- Stop / remove ---

    async def stop_container(self, container_id: str, stop_timeout: int = 10) -> None:
        if container_id not in self._containers:
            raise NotFoundError("container", container_id)
        self._containers[container_id]["State"]["Status"] = "exited"
        self._containers[container_id]["State"]["Running"] = False

    async def remove_container(self, container_id: str, force: bool = False) -> None:
        if container_id not in self._containers:
            raise NotFoundError("container", container_id)
        del self._containers[container_id]
        # Remove from networks
        for net_containers in self._networks.values():
            net_containers.discard(container_id)

    # --- Networks ---

    async def get_container_networks(self, container_id: str) -> list[str]:
        attrs = await self.get_container(container_id)
        networks = attrs.get("NetworkSettings", {}).get("Networks", {})
        return list(networks.keys())

    async def connect_to_network(self, container_id: str, network_name: str) -> None:
        if container_id not in self._containers:
            raise NotFoundError("container", container_id)
        attrs = self._containers[container_id]
        networks = attrs["NetworkSettings"]["Networks"]
        if network_name not in networks:
            networks[network_name] = {"NetworkID": f"net-{network_name}"}
        self._networks.setdefault(network_name, set()).add(container_id)
