"""Base model mixin with common fields."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class TimestampMixin(SQLModel):
    """Mixin that adds created_at and updated_at timestamps."""

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
