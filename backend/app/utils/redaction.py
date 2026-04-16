"""Log message redaction utility.

Strips known secret patterns from strings before logging.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_ReplaceFn = Callable[[re.Match[str]], str]

_PATTERNS: list[tuple[re.Pattern[str], _ReplaceFn]] = [
    # JWT-like tokens: keep first 6 and last 4
    (
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
        lambda m: m.group(0)[:6] + "•••" + m.group(0)[-4:],
    ),
    # Bearer tokens
    (
        re.compile(r"(Bearer\s+)\S{8,}"),
        lambda m: m.group(1) + "•••" + m.group(0)[-4:],
    ),
    # Cloudflare-style tokens
    (
        re.compile(r"[A-Za-z0-9_-]{36,}"),
        lambda m: m.group(0)[:4] + "•••" + m.group(0)[-4:] if len(m.group(0)) > 20 else m.group(0),
    ),
]


def redact(message: str) -> str:
    """Redact known secret patterns from a message string."""
    for pattern, replacement in _PATTERNS:
        message = pattern.sub(replacement, message)
    return message
