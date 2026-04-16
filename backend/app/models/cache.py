"""Cache models: zone_cache and container_cache."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class ZoneCache(SQLModel, table=True):
    """Cached zone list from CF for fast hostname resolution."""

    __tablename__ = "zone_cache"

    zone_id: str = Field(primary_key=True)
    zone_name: str = Field(nullable=False, index=True)
    account_id: str = Field(nullable=False, index=True)
    plan_name: str | None = Field(default=None)
    status: str | None = Field(default=None)
    refreshed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )


class ContainerCache(SQLModel, table=True):
    """Cache of known containers for compose-project lookups."""

    __tablename__ = "container_cache"

    container_id: str = Field(primary_key=True)
    container_name: str = Field(nullable=False)
    compose_project: str | None = Field(default=None)
    compose_service: str | None = Field(default=None)
    image: str | None = Field(default=None)
    status: str | None = Field(default=None)
    networks_json: str | None = Field(default=None)
    exposed_ports_json: str | None = Field(default=None)
    labels_json: str | None = Field(default=None)
    managed_by_tm: bool = Field(default=False, nullable=False)
    first_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    last_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
