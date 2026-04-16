"""Route model — one row per hostname served by a tunnel."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field

from app.models.base import TimestampMixin


class Route(TimestampMixin, table=True):
    """A hostname route served by a tunnel."""

    __tablename__ = "routes"

    id: int | None = Field(default=None, primary_key=True)
    tunnel_id: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("tunnels.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    # Matching criteria
    hostname: str = Field(nullable=False, index=True)
    path_regex: str | None = Field(default=None)
    priority: int = Field(nullable=False)
    # Destination
    target_compose_project: str | None = Field(default=None)
    target_compose_service: str | None = Field(default=None)
    target_container_name: str | None = Field(default=None)
    target_scheme: str = Field(default="http", nullable=False)
    target_port: int | None = Field(default=None)
    target_unix_socket_path: str | None = Field(default=None)
    target_path_prefix: str | None = Field(default=None)
    # Origin request options
    no_tls_verify: bool = Field(default=False, nullable=False)
    http_host_header: str | None = Field(default=None)
    origin_server_name: str | None = Field(default=None)
    connect_timeout_seconds: int = Field(default=30)
    tcp_keep_alive_seconds: int | None = Field(default=None)
    http2_origin: bool = Field(default=False, nullable=False)
    # DNS
    zone_id: str = Field(nullable=False)
    zone_name: str = Field(nullable=False)
    cf_dns_record_id: str | None = Field(default=None)
    dns_proxied: bool = Field(default=True, nullable=False)
    # Lifecycle
    enabled: bool = Field(default=True, nullable=False)
    status: str = Field(default="provisioning", nullable=False, index=True)
    status_detail: str | None = Field(default=None)
    last_healthy_at: datetime | None = Field(default=None)
    last_error_at: datetime | None = Field(default=None)
    last_error_message: str | None = Field(default=None)
