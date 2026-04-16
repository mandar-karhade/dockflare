"""Docker event listener and dispatcher."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

logger = structlog.get_logger()

EventHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class DockerEventDispatcher:
    """Routes Docker events to registered handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, dict[str, list[EventHandler]]] = {}

    def on(self, event_type: str, action: str, handler: EventHandler) -> None:
        """Register a handler for a specific event type + action."""
        self._handlers.setdefault(event_type, {}).setdefault(action, []).append(handler)

    async def dispatch(self, event: dict[str, Any]) -> None:
        """Dispatch an event to all matching handlers."""
        event_type = event.get("Type", "")
        action = event.get("Action", "").split(":")[0]  # e.g. "exec_start: ..." -> "exec_start"

        type_handlers = self._handlers.get(event_type, {})
        action_handlers = type_handlers.get(action, [])

        for handler in action_handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "docker_event.handler_error",
                    event_type=event_type,
                    action=action,
                )


async def start_event_listener(
    docker_client: Any,
    dispatcher: DockerEventDispatcher,
    queue: asyncio.Queue[dict[str, Any]] | None = None,
) -> None:
    """Long-running task that reads Docker events and dispatches them.

    If a queue is provided, events are also published there (for WebSocket fanout).
    """
    loop = asyncio.get_event_loop()
    event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def _thread_fn() -> None:
        try:
            for event in docker_client.events_generator():
                asyncio.run_coroutine_threadsafe(event_queue.put(event), loop)
        except Exception:
            logger.exception("docker_event.listener_thread_error")

    thread = threading.Thread(target=_thread_fn, daemon=True)
    thread.start()

    while True:
        event = await event_queue.get()
        await dispatcher.dispatch(event)
        if queue is not None:
            await queue.put(event)
