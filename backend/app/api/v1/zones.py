"""Zone discovery endpoint — lists CF zones from the API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import get_cf_client

router = APIRouter(prefix="/zones", tags=["zones"])


@router.get("")
async def list_zones(account_id: str | None = Query(default=None)) -> dict[str, Any]:
    """List all zones from Cloudflare."""
    client = get_cf_client()

    if not account_id:
        accounts = await client.list_accounts()
        if not accounts:
            return {"error": "No accounts found", "zones": []}
        account_id = accounts[0]["id"]

    zones = await client.list_zones(account_id)
    return {
        "account_id": account_id,
        "zones": [
            {
                "zone_id": z["id"],
                "zone_name": z["name"],
                "status": z.get("status", "unknown"),
                "plan": z.get("plan", {}).get("name", "unknown"),
            }
            for z in zones
        ],
        "total": len(zones),
    }
