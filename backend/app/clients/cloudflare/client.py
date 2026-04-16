"""Real CloudflareClient wrapping httpx.AsyncClient."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.clients.cloudflare.cache import TTLCache
from app.clients.cloudflare.retry import RateLimitError, cf_retry
from app.core.errors import CFAPIError

logger = structlog.get_logger()

CF_BASE_URL = "https://api.cloudflare.com/client/v4"


class CloudflareClient:
    """Async HTTP client for the Cloudflare API."""

    def __init__(self, token: str, base_url: str = CF_BASE_URL) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        self._zone_cache = TTLCache(ttl_seconds=900)  # 15 min
        self._account_cache = TTLCache(ttl_seconds=3600)  # 1 hour

    async def close(self) -> None:
        await self._client.aclose()

    # --- Low-level HTTP ---

    @cf_retry
    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Make an API request and handle errors/rate limits."""
        response = await self._client.request(method, path, **kwargs)

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", "1"))
            raise RateLimitError(retry_after=retry_after)

        data: dict[str, Any] = response.json()

        if not data.get("success", False):
            errors = data.get("errors", [])
            code = errors[0]["code"] if errors else response.status_code
            message = errors[0]["message"] if errors else "Unknown CF API error"
            raise CFAPIError(cf_code=code, message=message)

        return data

    async def get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return await self._request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return await self._request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return await self._request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return await self._request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return await self._request("DELETE", path, **kwargs)

    # --- Token verification ---

    async def verify_token(self) -> dict[str, Any]:
        """Verify the API token is valid."""
        data = await self.get("/user/tokens/verify")
        return data["result"]

    # --- Account operations ---

    async def list_accounts(self) -> list[dict[str, Any]]:
        cached = self._account_cache.get("accounts")
        if cached is not None:
            return cached
        data = await self.get("/accounts")
        result: list[dict[str, Any]] = data["result"]
        self._account_cache.set("accounts", result)
        return result

    # --- Zone operations ---

    async def list_zones(self, account_id: str) -> list[dict[str, Any]]:
        cache_key = f"zones:{account_id}"
        cached = self._zone_cache.get(cache_key)
        if cached is not None:
            return cached

        all_zones: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self.get(
                "/zones",
                params={"account.id": account_id, "per_page": 50, "page": page},
            )
            all_zones.extend(data["result"])
            info = data.get("result_info", {})
            if page >= info.get("total_pages", 1):
                break
            page += 1

        self._zone_cache.set(cache_key, all_zones)
        return all_zones

    # --- Tunnel operations ---

    async def list_tunnels(self, account_id: str) -> list[dict[str, Any]]:
        data = await self.get(
            f"/accounts/{account_id}/cfd_tunnel",
            params={"is_deleted": "false"},
        )
        return data["result"]

    async def create_tunnel(self, account_id: str, name: str) -> dict[str, Any]:
        data = await self.post(
            f"/accounts/{account_id}/cfd_tunnel",
            json={"name": name, "config_src": "cloudflare"},
        )
        return data["result"]

    async def get_tunnel(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        data = await self.get(f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}")
        return data["result"]

    async def delete_tunnel(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        data = await self.delete(f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}")
        return data["result"]

    async def get_tunnel_token(self, account_id: str, tunnel_id: str) -> str:
        data = await self.get(f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token")
        return data["result"]

    async def get_tunnel_connections(self, account_id: str, tunnel_id: str) -> list[dict[str, Any]]:
        data = await self.get(f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/connections")
        return data["result"]

    # --- Tunnel configuration (ingress) ---

    async def get_tunnel_config(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        data = await self.get(f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations")
        return data["result"]

    async def update_tunnel_config(
        self, account_id: str, tunnel_id: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        data = await self.put(
            f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
            json={"config": config},
        )
        return data["result"]

    # --- DNS operations ---

    async def list_dns_records(
        self, zone_id: str, name: str | None = None, record_type: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if name:
            params["name"] = name
        if record_type:
            params["type"] = record_type
        data = await self.get(f"/zones/{zone_id}/dns_records", params=params)
        return data["result"]

    async def create_dns_record(
        self,
        zone_id: str,
        *,
        record_type: str,
        name: str,
        content: str,
        proxied: bool = True,
        ttl: int = 1,
        comment: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": record_type,
            "name": name,
            "content": content,
            "proxied": proxied,
            "ttl": ttl,
        }
        if comment:
            body["comment"] = comment
        data = await self.post(f"/zones/{zone_id}/dns_records", json=body)
        return data["result"]

    async def update_dns_record(
        self,
        zone_id: str,
        record_id: str,
        *,
        record_type: str | None = None,
        name: str | None = None,
        content: str | None = None,
        proxied: bool | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if record_type is not None:
            body["type"] = record_type
        if name is not None:
            body["name"] = name
        if content is not None:
            body["content"] = content
        if proxied is not None:
            body["proxied"] = proxied
        if comment is not None:
            body["comment"] = comment
        data = await self.patch(f"/zones/{zone_id}/dns_records/{record_id}", json=body)
        return data["result"]

    async def delete_dns_record(self, zone_id: str, record_id: str) -> dict[str, Any]:
        data = await self.delete(f"/zones/{zone_id}/dns_records/{record_id}")
        return data["result"]
