"""Zone response schemas."""

from __future__ import annotations

from pydantic import BaseModel


class ZoneRead(BaseModel):
    zone_id: str
    zone_name: str
    account_id: str
    plan_name: str | None
    status: str | None
