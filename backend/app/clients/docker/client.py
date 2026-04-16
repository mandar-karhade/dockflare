"""Real DockerClient wrapper — all sync docker-py calls wrapped in asyncio.to_thread."""

from __future__ import annotations

import asyncio
from typing import Any

import docker
import structlog

from app.core import labels as lbl

logger = structlog.get_logger()


class DockerClient:
    """Async wrapper around docker-py's synchronous client."""

    def __init__(self, base_url: str = "unix:///var/run/docker.sock") -> None:
        self._client = docker.DockerClient(base_url=base_url)

    async def close(self) -> None:
        await asyncio.to_thread(self._client.close)

    async def ping(self) -> bool:
        return await asyncio.to_thread(self._client.ping)

    # --- Containers ---

    async def list_containers(
        self, *, all: bool = True, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        def _list() -> list[dict[str, Any]]:
            containers = self._client.containers.list(all=all, filters=filters or {})
            return [c.attrs for c in containers]

        return await asyncio.to_thread(_list)

    async def get_container(self, container_id: str) -> dict[str, Any]:
        def _get() -> dict[str, Any]:
            c = self._client.containers.get(container_id)
            c.reload()
            return c.attrs  # type: ignore[return-value]

        return await asyncio.to_thread(_get)

    async def find_by_compose(
        self, project: str, service: str | None = None
    ) -> list[dict[str, Any]]:
        filters: dict[str, list[str]] = {"label": [f"{lbl.COMPOSE_PROJECT}={project}"]}
        if service:
            filters["label"].append(f"{lbl.COMPOSE_SERVICE}={service}")
        return await self.list_containers(filters=filters)

    async def find_by_label(self, label: str, value: str) -> list[dict[str, Any]]:
        return await self.list_containers(filters={"label": [f"{label}={value}"]})

    async def list_managed_sidecars(self) -> list[dict[str, Any]]:
        return await self.list_containers(filters={"label": [f"{lbl.MANAGED}=true"]})

    # --- Spawn cloudflared sidecar ---

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
        def _spawn() -> dict[str, Any]:
            primary_network = networks[0] if networks else "bridge"
            container = self._client.containers.run(
                image=image,
                name=name,
                command=["tunnel", "--no-autoupdate", "run"],
                environment={"TUNNEL_TOKEN": token},
                network=primary_network,
                labels={**labels, lbl.MANAGED: "true"},
                restart_policy={"Name": "unless-stopped"},
                detach=True,
                mem_limit=mem_limit,
                memswap_limit=mem_limit,
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                log_config={
                    "Type": "json-file",
                    "Config": {"max-size": "10m", "max-file": "3"},
                },
            )
            # Attach to additional networks
            for net in networks[1:]:
                network_obj = self._client.networks.get(net)
                network_obj.connect(container)

            container.reload()
            return container.attrs  # type: ignore[return-value]

        return await asyncio.to_thread(_spawn)

    # --- Stop / remove ---

    async def stop_container(self, container_id: str, stop_timeout: int = 10) -> None:
        def _stop() -> None:
            try:
                c = self._client.containers.get(container_id)
                c.stop(timeout=stop_timeout)
            except docker.errors.APIError as e:
                if "is not running" not in str(e):
                    raise

        await asyncio.to_thread(_stop)

    async def remove_container(self, container_id: str, force: bool = False) -> None:
        def _remove() -> None:
            c = self._client.containers.get(container_id)
            c.remove(force=force)

        await asyncio.to_thread(_remove)

    # --- Networks ---

    async def get_container_networks(self, container_id: str) -> list[str]:
        attrs = await self.get_container(container_id)
        networks = attrs.get("NetworkSettings", {}).get("Networks", {})
        return list(networks.keys())

    async def connect_to_network(self, container_id: str, network_name: str) -> None:
        def _connect() -> None:
            network = self._client.networks.get(network_name)
            network.connect(container_id)

        await asyncio.to_thread(_connect)

    # --- Events (blocking generator, meant for a thread) ---

    def events_generator(self, filters: dict[str, Any] | None = None) -> Any:
        """Returns a blocking event generator. Run in a dedicated thread."""
        return self._client.events(
            decode=True,
            filters=filters or {"type": ["container", "network"]},
        )
