"""Tunnel service — create/delete CF tunnels and manage cloudflared sidecars."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.docker.helpers import resolve_compose_networks, sidecar_name
from app.core import labels as lbl
from app.core.audit import audit_context
from app.core.errors import NotFoundError
from app.core.vault import VaultService
from app.models.tunnel import Tunnel
from app.utils.time import utcnow

logger = structlog.get_logger()


class TunnelService:
    """Business logic for tunnel CRUD and sidecar management."""

    def __init__(
        self,
        db: AsyncSession,
        vault: VaultService,
        cf_client: Any,
        docker_client: Any,
        credential_id: int,
        account_id: str,
    ) -> None:
        self._db = db
        self._vault = vault
        self._cf = cf_client
        self._docker = docker_client
        self._credential_id = credential_id
        self._account_id = account_id

    async def create(
        self,
        *,
        name: str,
        rotation_policy: str = "manual",
        primary_compose_project: str | None = None,
        primary_compose_service: str | None = None,
        cloudflared_image: str = "cloudflare/cloudflared:2024.10.0",
        actor: str = "system",
    ) -> Tunnel:
        """Create a CF tunnel, fetch token, spawn sidecar, record in DB."""
        async with audit_context(
            db=self._db,
            actor=actor,
            action="tunnel.create",
            entity_type="tunnel",
        ) as ctx:
            # 1. Create CF tunnel
            cf_tunnel = await self._cf.create_tunnel(self._account_id, name)
            cf_tunnel_id = cf_tunnel["id"]

            # 2. Fetch tunnel token
            token = await self._cf.get_tunnel_token(self._account_id, cf_tunnel_id)

            # 3. Insert DB row with provisioning status
            tunnel = Tunnel(
                cf_tunnel_id=cf_tunnel_id,
                cf_tunnel_name=name,
                cf_credential_id=self._credential_id,
                account_id=self._account_id,
                token_encrypted=self._vault.encrypt(token),
                token_last_four=token[-4:],
                token_fetched_at=utcnow(),
                rotation_policy=rotation_policy,
                primary_compose_project=primary_compose_project,
                primary_compose_service=primary_compose_service,
                cloudflared_image=cloudflared_image,
                status="provisioning",
            )
            self._db.add(tunnel)
            await self._db.commit()
            await self._db.refresh(tunnel)

            # 4. Spawn cloudflared sidecar
            project = primary_compose_project or "standalone"
            service = primary_compose_service or name
            container_name = sidecar_name(project, service)

            sidecar_labels = {
                lbl.TUNNEL_ID: str(tunnel.id),
                lbl.TUNNEL_CF_ID: cf_tunnel_id,
                lbl.TUNNEL_NAME: name,
                lbl.TARGET_PROJECT: project,
                lbl.TARGET_SERVICE: service,
                lbl.SIDECAR_ROLE: "cloudflared-sidecar",
            }

            networks = await resolve_compose_networks(
                self._docker,
                primary_compose_project,
                primary_compose_service,
            )

            sidecar_attrs = await self._docker.spawn_cloudflared(
                name=container_name,
                image=cloudflared_image,
                token=token,
                networks=networks,
                labels=sidecar_labels,
            )

            # 5. Update DB with sidecar info
            tunnel.cloudflared_container_id = sidecar_attrs["Id"]
            tunnel.token_deployed_at = utcnow()
            tunnel.status = "active"
            await self._db.commit()
            await self._db.refresh(tunnel)

            ctx.set_entity_id(tunnel.id)
            ctx.set_after({"id": tunnel.id, "cf_tunnel_id": cf_tunnel_id, "status": "active"})

            logger.info("tunnel.created", tunnel_id=tunnel.id, cf_id=cf_tunnel_id)
            return tunnel

    async def get(self, tunnel_id: int) -> Tunnel:
        """Get a tunnel by DB id."""
        result = await self._db.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
        tunnel = result.scalar_one_or_none()
        if tunnel is None:
            raise NotFoundError("tunnel", tunnel_id)
        return tunnel

    async def list_all(self) -> list[Tunnel]:
        """List all tunnels."""
        result = await self._db.execute(select(Tunnel).order_by(Tunnel.created_at.desc()))
        return list(result.scalars().all())

    async def delete(self, tunnel_id: int, *, actor: str = "system") -> None:
        """Delete a tunnel: stop sidecar, delete CF tunnel, remove DB row."""
        tunnel = await self.get(tunnel_id)

        async with audit_context(
            db=self._db,
            actor=actor,
            action="tunnel.delete",
            entity_type="tunnel",
            entity_id=tunnel_id,
            before={"id": tunnel_id, "cf_tunnel_id": tunnel.cf_tunnel_id},
        ):
            # 1. Stop and remove sidecar
            if tunnel.cloudflared_container_id:
                try:
                    await self._docker.stop_container(tunnel.cloudflared_container_id)
                    await self._docker.remove_container(tunnel.cloudflared_container_id, force=True)
                except Exception:
                    logger.warning(
                        "tunnel.sidecar_cleanup_failed",
                        tunnel_id=tunnel_id,
                        container_id=tunnel.cloudflared_container_id,
                    )

            # 2. Delete CF tunnel
            try:
                await self._cf.delete_tunnel(self._account_id, tunnel.cf_tunnel_id)
            except Exception:
                logger.warning(
                    "tunnel.cf_delete_failed",
                    tunnel_id=tunnel_id,
                    cf_id=tunnel.cf_tunnel_id,
                )

            # 3. Remove DB row
            await self._db.delete(tunnel)
            await self._db.commit()

            logger.info("tunnel.deleted", tunnel_id=tunnel_id)
