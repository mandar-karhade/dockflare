"""Audit log model — generic append-only log for all state changes."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class AuditLog(SQLModel, table=True):
    """Append-only log for all state-changing actions."""

    __tablename__ = "audit_log"

    id: int | None = Field(default=None, primary_key=True)
    actor: str = Field(nullable=False)
    action: str = Field(nullable=False, index=True)
    entity_type: str | None = Field(default=None)
    entity_id: int | None = Field(default=None)
    request_id: str | None = Field(default=None)
    before_json: str | None = Field(default=None)
    after_json: str | None = Field(default=None)
    status: str = Field(nullable=False)
    error_message: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
