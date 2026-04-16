"""DNS comment prefix constants.

Used to identify DNS records managed by Tunnel Manager.
"""

PREFIX = "tunnel-manager"


def route_comment(route_id: int) -> str:
    """Comment string for a DNS record owned by a route."""
    return f"{PREFIX}:route:{route_id}"


def is_managed(comment: str | None) -> bool:
    """Check if a DNS record comment indicates TM ownership."""
    return comment is not None and comment.startswith(f"{PREFIX}:")


def parse_route_id(comment: str) -> int | None:
    """Extract route ID from a tunnel-manager comment, or None."""
    if not is_managed(comment):
        return None
    parts = comment.split(":")
    if len(parts) >= 3 and parts[1] == "route":
        try:
            return int(parts[2])
        except ValueError:
            return None
    return None
