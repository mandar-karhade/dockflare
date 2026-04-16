"""Tunnel model — one row per CF tunnel managed by the system."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field

from app.models.base import TimestampMixin


class Tunnel(TimestampMixin, table=True):
    """A Cloudflare tunnel managed by the system."""

    __tablename__ = "tunnels"

    id: int | None = Field(default=None, primary_key=True)
    cf_tunnel_id: str = Field(unique=True, nullable=False, index=True)
    cf_tunnel_name: str = Field(nullable=False)
    cf_credential_id: int = Field(foreign_key="cf_credentials.id", nullable=False)
    account_id: str = Field(nullable=False)
    token_encrypted: bytes = Field(nullable=False)
    token_last_four: str = Field(nullable=False)
    token_fetched_at: datetime = Field(nullable=False)
    token_deployed_at: datetime | None = Field(default=None)
    # Primary target (informational)
    primary_compose_project: str | None = Field(default=None)
    primary_compose_service: str | None = Field(default=None)
    cloudflared_container_id: str | None = Field(default=None)
    cloudflared_image: str = Field(default="cloudflare/cloudflared:2024.10.0")
    # Rotation
    rotation_policy: str = Field(default="manual", nullable=False)
    next_rotation_due: datetime | None = Field(default=None)
    last_rotation_at: datetime | None = Field(default=None)
    last_rotation_status: str | None = Field(default=None)
    # Lifecycle
    status: str = Field(default="active", nullable=False, index=True)
    error_message: str | None = Field(default=None)
