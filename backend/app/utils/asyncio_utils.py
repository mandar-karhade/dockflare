"""Async utilities including the per-entity lock registry."""

from __future__ import annotations

import asyncio


class EntityLockRegistry:
    """Per-entity-id async locks. Prevents concurrent ops on same tunnel/route."""

    def __init__(self) -> None:
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    async def acquire(self, entity_type: str, entity_id: int) -> asyncio.Lock:
        """Get or create a lock for the given entity."""
        async with self._registry_lock:
            key = (entity_type, entity_id)
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]
