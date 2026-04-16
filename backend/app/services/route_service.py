"""Route service — create/delete routes with DNS and ingress management."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_context
from app.core.comments import route_comment
from app.core.errors import NotFoundError
from app.core.vault import VaultService
from app.models.route import Route
from app.models.tunnel import Tunnel
from app.services.ingress_builder import build_ingress
from app.services.priority_calculator import calculate_priority

logger = structlog.get_logger()


class RouteService:
    """Business logic for route CRUD, DNS, and ingress sync."""

    def __init__(
        self,
        db: AsyncSession,
        vault: VaultService,
        cf_client: Any,
        docker_client: Any,
        account_id: str,
    ) -> None:
        self._db = db
        self._vault = vault
        self._cf = cf_client
        self._docker = docker_client
        self._account_id = account_id

    async def create(
        self,
        *,
        tunnel_id: int,
        hostname: str,
        target_compose_project: str | None = None,
        target_compose_service: str | None = None,
        target_container_name: str | None = None,
        target_scheme: str = "http",
        target_port: int | None = None,
        target_unix_socket_path: str | None = None,
        target_path_prefix: str | None = None,
        no_tls_verify: bool = False,
        http_host_header: str | None = None,
        origin_server_name: str | None = None,
        connect_timeout_seconds: int = 30,
        http2_origin: bool = False,
        path_regex: str | None = None,
        dns_proxied: bool = True,
        actor: str = "system",
    ) -> Route:
        """Create a route: DNS record + ingress update + DB row."""
        # Verify tunnel exists
        tunnel_result = await self._db.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
        tunnel = tunnel_result.scalar_one_or_none()
        if tunnel is None:
            raise NotFoundError("tunnel", tunnel_id)

        async with audit_context(
            db=self._db,
            actor=actor,
            action="route.create",
            entity_type="route",
        ) as ctx:
            # 1. Resolve zone
            zone_id, zone_name = await self._resolve_zone(hostname)

            # 2. Calculate priority
            priority = calculate_priority(hostname, path_regex)

            # 3. Insert route with provisioning status
            route = Route(
                tunnel_id=tunnel_id,
                hostname=hostname,
                path_regex=path_regex,
                priority=priority,
                target_compose_project=target_compose_project,
                target_compose_service=target_compose_service,
                target_container_name=target_container_name,
                target_scheme=target_scheme,
                target_port=target_port,
                target_unix_socket_path=target_unix_socket_path,
                target_path_prefix=target_path_prefix,
                no_tls_verify=no_tls_verify,
                http_host_header=http_host_header,
                origin_server_name=origin_server_name,
                connect_timeout_seconds=connect_timeout_seconds,
                http2_origin=http2_origin,
                zone_id=zone_id,
                zone_name=zone_name,
                dns_proxied=dns_proxied,
                status="provisioning",
            )
            self._db.add(route)
            await self._db.commit()
            await self._db.refresh(route)

            # 4. Sync ingress to CF
            await self._sync_tunnel_ingress(tunnel_id)

            # 5. Create DNS record
            cname_target = f"{tunnel.cf_tunnel_id}.cfargotunnel.com"
            dns_record = await self._cf.create_dns_record(
                zone_id,
                record_type="CNAME",
                name=hostname,
                content=cname_target,
                proxied=dns_proxied,
                comment=route_comment(route.id),  # type: ignore[arg-type]
            )

            # 6. Update route to active
            route.cf_dns_record_id = dns_record["id"]
            route.status = "active"
            await self._db.commit()
            await self._db.refresh(route)

            ctx.set_entity_id(route.id)
            ctx.set_after({"id": route.id, "hostname": hostname, "status": "active"})

            logger.info("route.created", route_id=route.id, hostname=hostname)
            return route

    async def get(self, route_id: int) -> Route:
        result = await self._db.execute(select(Route).where(Route.id == route_id))
        route = result.scalar_one_or_none()
        if route is None:
            raise NotFoundError("route", route_id)
        return route

    async def list_all(self, tunnel_id: int | None = None) -> list[Route]:
        stmt = select(Route).order_by(Route.priority)
        if tunnel_id is not None:
            stmt = stmt.where(Route.tunnel_id == tunnel_id)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, route_id: int, *, actor: str = "system") -> None:
        """Delete a route: remove DNS, update ingress, remove DB row."""
        route = await self.get(route_id)

        async with audit_context(
            db=self._db,
            actor=actor,
            action="route.delete",
            entity_type="route",
            entity_id=route_id,
            before={"id": route_id, "hostname": route.hostname},
        ):
            # 1. Delete DNS record
            if route.cf_dns_record_id:
                try:
                    await self._cf.delete_dns_record(route.zone_id, route.cf_dns_record_id)
                except Exception:
                    logger.warning(
                        "route.dns_delete_failed",
                        route_id=route_id,
                        record_id=route.cf_dns_record_id,
                    )

            # 2. Remove DB row
            tunnel_id = route.tunnel_id
            await self._db.delete(route)
            await self._db.commit()

            # 3. Sync ingress (without the deleted route)
            await self._sync_tunnel_ingress(tunnel_id)

            logger.info("route.deleted", route_id=route_id)

    async def _resolve_zone(self, hostname: str) -> tuple[str, str]:
        """Find the matching zone for a hostname."""
        zones = await self._cf.list_zones(self._account_id)

        # Strip wildcard prefix
        search_hostname = hostname.lstrip("*.")

        parts = search_hostname.split(".")
        zone_names = {z["name"]: z for z in zones}

        # Try longest suffix first
        for i in range(len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in zone_names:
                zone = zone_names[candidate]
                return zone["id"], zone["name"]

        available = sorted(zone_names.keys())
        msg = f"No zone matches {hostname}. Available: {available}"
        raise NotFoundError("zone", msg)

    async def _sync_tunnel_ingress(self, tunnel_id: int) -> None:
        """Rebuild and push ingress config for a tunnel."""
        routes = await self.list_all(tunnel_id=tunnel_id)
        config = build_ingress(routes)

        # Get tunnel CF ID
        tunnel_result = await self._db.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
        tunnel = tunnel_result.scalar_one_or_none()
        if tunnel is None:
            return

        await self._cf.update_tunnel_config(self._account_id, tunnel.cf_tunnel_id, config)
        logger.info(
            "ingress.synced",
            tunnel_id=tunnel_id,
            route_count=len(routes),
        )
