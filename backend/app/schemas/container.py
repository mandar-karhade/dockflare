"""Container response schemas."""

from __future__ import annotations

from pydantic import BaseModel


class ContainerRead(BaseModel):
    container_id: str
    container_name: str
    compose_project: str | None
    compose_service: str | None
    image: str | None
    status: str | None
    networks: list[str]
    exposed_ports: list[int]
    managed_by_tm: bool
