"""Datetime helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(UTC)
