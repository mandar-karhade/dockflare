"""Build CF ingress configuration from DB routes.

Always ends with catch-all {"service": "http_status:404"}.
"""

from __future__ import annotations

from typing import Any

from app.models.route import Route


def build_ingress(routes: list[Route]) -> dict[str, Any]:
    """Convert DB routes to a CF tunnel ingress config dict.

    Routes should be pre-sorted by priority (ascending).
    """
    rules: list[dict[str, Any]] = []

    for route in routes:
        if not route.enabled:
            continue

        rule: dict[str, Any] = {}

        # Hostname
        rule["hostname"] = route.hostname

        # Path regex
        if route.path_regex:
            rule["path"] = route.path_regex

        # Service URL
        service_url = _build_service_url(route)
        rule["service"] = service_url

        # Origin request options (only include non-defaults)
        origin_request = _build_origin_request(route)
        if origin_request:
            rule["originRequest"] = origin_request

        rules.append(rule)

    # Catch-all (always last)
    rules.append({"service": "http_status:404"})

    return {
        "ingress": rules,
        "warp-routing": {"enabled": False},
    }


def _build_service_url(route: Route) -> str:
    """Construct the service URL for a route."""
    scheme = route.target_scheme

    if scheme == "unix" and route.target_unix_socket_path:
        return f"unix:{route.target_unix_socket_path}"

    # Build host from compose identity or container name
    host = route.target_compose_service or route.target_container_name or "localhost"

    port_str = f":{route.target_port}" if route.target_port else ""
    path_str = route.target_path_prefix or ""

    return f"{scheme}://{host}{port_str}{path_str}"


def _build_origin_request(route: Route) -> dict[str, Any]:
    """Build originRequest block, only including non-default values."""
    req: dict[str, Any] = {}

    if route.no_tls_verify:
        req["noTLSVerify"] = True
    if route.http_host_header:
        req["httpHostHeader"] = route.http_host_header
    if route.origin_server_name:
        req["originServerName"] = route.origin_server_name
    if route.connect_timeout_seconds != 30:
        req["connectTimeout"] = f"{route.connect_timeout_seconds}s"
    if route.tcp_keep_alive_seconds:
        req["tcpKeepAlive"] = f"{route.tcp_keep_alive_seconds}s"
    if route.http2_origin:
        req["http2Origin"] = True

    return req
