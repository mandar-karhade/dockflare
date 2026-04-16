"""Tunnel request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TunnelCreate(BaseModel):
    name: str
    rotation_policy: str = "manual"
    primary_compose_project: str | None = None
    primary_compose_service: str | None = None
    cloudflared_image: str | None = None


class TunnelRead(BaseModel):
    id: int
    cf_tunnel_id: str
    cf_tunnel_name: str
    cf_credential_id: int
    account_id: str
    token_last_four: str
    primary_compose_project: str | None
    primary_compose_service: str | None
    cloudflared_container_id: str | None
    cloudflared_image: str
    rotation_policy: str
    next_rotation_due: datetime | None
    last_rotation_at: datetime | None
    last_rotation_status: str | None
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
