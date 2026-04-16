"""In-process pub/sub for WebSocket fanout."""

from __future__ import annotations

import asyncio
import contextlib


class EventBroker:
    """Broadcast events to all connected WebSocket subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()

    async def publish(self, event: dict[str, object]) -> None:
        """Send an event to all subscribers, dropping for slow consumers."""
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[dict[str, object]]:
        """Create a new subscriber queue."""
        q: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, object]]) -> None:
        """Remove a subscriber queue."""
        self._subscribers.discard(q)
