"""Error response schemas following RFC 7807 Problem Details."""

from __future__ import annotations

from pydantic import BaseModel


class ProblemDetail(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


class ConflictDetail(ProblemDetail):
    conflict_type: str | None = None
    existing_record: dict[str, object] | None = None
    resolution_options: list[str] | None = None
