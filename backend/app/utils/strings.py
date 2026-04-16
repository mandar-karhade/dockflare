"""String utilities for hostname parsing and validation."""

from __future__ import annotations

import re

_HOSTNAME_RE = re.compile(r"^(\*\.)?([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$")


def is_valid_hostname(hostname: str) -> bool:
    """Validate a hostname (including wildcard like *.example.com)."""
    return _HOSTNAME_RE.match(hostname) is not None


def extract_zone_name(hostname: str) -> str:
    """Extract the registrable domain from a hostname.

    e.g. 'api.v2.example.com' -> 'example.com'
         '*.example.com' -> 'example.com'
    """
    hostname = hostname.lstrip("*.")
    parts = hostname.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return hostname
