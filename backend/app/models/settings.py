"""App settings key-value store."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class AppSetting(SQLModel, table=True):
    """Key-value store for app-level settings."""

    __tablename__ = "app_settings"

    key: str = Field(primary_key=True)
    value: str = Field(nullable=False)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
