"""Rotation event model — audit trail for all token rotations."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class RotationEvent(SQLModel, table=True):
    """Audit trail for token rotations."""

    __tablename__ = "rotation_events"

    id: int | None = Field(default=None, primary_key=True)
    entity_type: str = Field(nullable=False)
    entity_id: int = Field(nullable=False, index=True)
    triggered_by: str = Field(nullable=False)
    started_at: datetime = Field(nullable=False)
    completed_at: datetime | None = Field(default=None)
    status: str = Field(nullable=False, index=True)
    old_token_last_four: str | None = Field(default=None)
    new_token_last_four: str | None = Field(default=None)
    downtime_seconds: float | None = Field(default=None)
    error_message: str | None = Field(default=None)
    details_json: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
