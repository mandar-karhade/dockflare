"""Drift finding model — output of periodic drift detection scans."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class DriftFinding(SQLModel, table=True):
    """Output of periodic drift detection scans."""

    __tablename__ = "drift_findings"

    id: int | None = Field(default=None, primary_key=True)
    scan_id: str = Field(nullable=False, index=True)
    finding_type: str = Field(nullable=False)
    entity_type: str = Field(nullable=False)
    entity_id: int = Field(nullable=False)
    severity: str = Field(default="warning", nullable=False)
    expected_value: str | None = Field(default=None)
    actual_value: str | None = Field(default=None)
    details_json: str | None = Field(default=None)
    detected_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    resolution: str | None = Field(default=None)
    resolved_at: datetime | None = Field(default=None)
    resolved_by: str | None = Field(default=None)
