"""In-memory caches for zone and account data from CF."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    data: Any
    expires_at: float


class TTLCache:
    """Simple TTL-based in-memory cache."""

    def __init__(self, ttl_seconds: float = 900.0) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None or time.monotonic() > entry.expires_at:
            self._store.pop(key, None)
            return None
        return entry.data

    def set(self, key: str, value: Any) -> None:
        self._store[key] = CacheEntry(
            data=value,
            expires_at=time.monotonic() + self._ttl,
        )

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()
