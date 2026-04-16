"""Route request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RouteCreate(BaseModel):
    tunnel_id: int
    hostname: str
    target_compose_project: str | None = None
    target_compose_service: str | None = None
    target_container_name: str | None = None
    target_scheme: str = "http"
    target_port: int | None = None
    target_unix_socket_path: str | None = None
    target_path_prefix: str | None = None
    no_tls_verify: bool = False
    http_host_header: str | None = None
    origin_server_name: str | None = None
    connect_timeout_seconds: int = 30
    http2_origin: bool = False
    path_regex: str | None = None
    dns_proxied: bool = True
    conflict_resolution: str | None = None


class RouteRead(BaseModel):
    id: int
    tunnel_id: int
    hostname: str
    path_regex: str | None
    priority: int
    target_compose_project: str | None
    target_compose_service: str | None
    target_container_name: str | None
    target_scheme: str
    target_port: int | None
    zone_id: str
    zone_name: str
    cf_dns_record_id: str | None
    dns_proxied: bool
    enabled: bool
    status: str
    status_detail: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
