"""Structlog configuration with secret redaction."""

from __future__ import annotations

import logging
import re
from collections.abc import MutableMapping
from typing import Any

import structlog

_SECRET_PATTERNS = [
    re.compile(r"(eyJ[A-Za-z0-9_-]{20,})\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT-like
    re.compile(r"(Bearer\s+)\S+"),
    re.compile(r"(sk_live_|sk_test_)\w+"),
    re.compile(r"(TUNNEL_TOKEN=)\S+"),
]


def _redact_secrets(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Redact known secret patterns from log messages."""
    msg = str(event_dict.get("event", ""))
    for pattern in _SECRET_PATTERNS:
        msg = pattern.sub(lambda m: m.group(0)[:6] + "•••" + m.group(0)[-4:], msg)
    event_dict["event"] = msg
    return event_dict


def setup_logging(log_level: str = "info") -> None:
    """Configure structlog with JSON output and redaction."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            _redact_secrets,
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, log_level.upper(), logging.INFO),
    )
