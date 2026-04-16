"""Priority calculator for ingress rule ordering.

Lower priority = matched first. More specific rules get lower numbers.
"""

from __future__ import annotations


def calculate_priority(hostname: str, path_regex: str | None = None) -> int:
    """Calculate ingress priority for a route.

    Factors:
    - Path regex routes rank highest (lowest number) ~200-400
    - Exact hosts rank mid-range ~600-900
    - Wildcard hosts rank lowest ~900-1100
    - Within each tier, more hostname segments = more specific = lower priority
    """
    base = 800

    # Wildcard penalty
    if hostname.startswith("*."):
        base = 1000
        hostname = hostname[2:]  # strip wildcard for segment counting

    # Count segments — more segments = more specific = lower priority
    segments = hostname.count(".") + 1
    specificity_bonus = segments * 50

    # Path regex bonus — these should match first
    path_bonus = 500 if path_regex else 0

    priority = base - specificity_bonus - path_bonus

    # Clamp to positive range
    return max(1, priority)
