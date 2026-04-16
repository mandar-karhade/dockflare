"""DNS-related models: operations, backups, conflicts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class DnsOperation(SQLModel, table=True):
    """Audit trail for all DNS mutations."""

    __tablename__ = "dns_operations"

    id: int | None = Field(default=None, primary_key=True)
    operation: str = Field(nullable=False)
    zone_id: str = Field(nullable=False)
    cf_record_id: str | None = Field(default=None)
    hostname: str = Field(nullable=False, index=True)
    record_type: str | None = Field(default=None)
    old_content: str | None = Field(default=None)
    new_content: str | None = Field(default=None)
    old_proxied: bool | None = Field(default=None)
    new_proxied: bool | None = Field(default=None)
    triggered_by: str = Field(nullable=False)
    route_id: int | None = Field(default=None, foreign_key="routes.id")
    status: str = Field(nullable=False)
    error_message: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )


class DnsBackup(SQLModel, table=True):
    """Snapshot of DNS records replaced by manager operations."""

    __tablename__ = "dns_backups"

    id: int | None = Field(default=None, primary_key=True)
    zone_id: str = Field(nullable=False)
    hostname: str = Field(nullable=False, index=True)
    record_type: str = Field(nullable=False)
    record_content: str = Field(nullable=False)
    record_ttl: int | None = Field(default=None)
    record_proxied: bool | None = Field(default=None)
    record_comment: str | None = Field(default=None)
    backed_up_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    reason: str = Field(nullable=False)
    related_route_id: int | None = Field(default=None, foreign_key="routes.id")
    restored_at: datetime | None = Field(default=None)
    retention_until: datetime | None = Field(default=None)


class DnsConflict(SQLModel, table=True):
    """Record of conflict situations and their resolutions."""

    __tablename__ = "dns_conflicts"

    id: int | None = Field(default=None, primary_key=True)
    hostname: str = Field(nullable=False)
    zone_id: str = Field(nullable=False)
    conflict_type: str = Field(nullable=False)
    existing_record_snapshot: str | None = Field(default=None)
    expected_state: str | None = Field(default=None)
    detected_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    resolution: str | None = Field(default=None)
    resolved_by: str | None = Field(default=None)
    resolved_at: datetime | None = Field(default=None)
    dns_backup_id: int | None = Field(default=None, foreign_key="dns_backups.id")
    route_id: int | None = Field(default=None, foreign_key="routes.id")
