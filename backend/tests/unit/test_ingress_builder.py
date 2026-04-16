"""Unit tests for ingress builder."""

from __future__ import annotations

from app.models.route import Route
from app.services.ingress_builder import build_ingress


def _make_route(**kwargs) -> Route:
    """Create a Route with sensible defaults for testing."""
    defaults = {
        "id": 1,
        "tunnel_id": 1,
        "hostname": "app.example.com",
        "priority": 100,
        "target_scheme": "http",
        "target_compose_service": "web",
        "target_port": 3000,
        "zone_id": "z",
        "zone_name": "example.com",
        "enabled": True,
        "status": "active",
        "no_tls_verify": False,
        "http2_origin": False,
        "dns_proxied": True,
        "connect_timeout_seconds": 30,
    }
    defaults.update(kwargs)
    return Route(**defaults)


def test_single_route_with_catchall():
    routes = [_make_route()]
    config = build_ingress(routes)
    ingress = config["ingress"]

    assert len(ingress) == 2
    assert ingress[0]["hostname"] == "app.example.com"
    assert ingress[0]["service"] == "http://web:3000"
    assert ingress[-1] == {"service": "http_status:404"}


def test_empty_routes_still_has_catchall():
    config = build_ingress([])
    assert config["ingress"] == [{"service": "http_status:404"}]


def test_disabled_routes_excluded():
    routes = [
        _make_route(id=1, hostname="a.example.com", enabled=True),
        _make_route(id=2, hostname="b.example.com", enabled=False),
    ]
    config = build_ingress(routes)
    hostnames = [r.get("hostname") for r in config["ingress"] if "hostname" in r]
    assert hostnames == ["a.example.com"]


def test_path_regex_included():
    routes = [_make_route(path_regex="^/api/.*")]
    config = build_ingress(routes)
    assert config["ingress"][0]["path"] == "^/api/.*"


def test_origin_request_included_when_non_default():
    routes = [_make_route(no_tls_verify=True, http2_origin=True)]
    config = build_ingress(routes)
    origin = config["ingress"][0]["originRequest"]
    assert origin["noTLSVerify"] is True
    assert origin["http2Origin"] is True


def test_origin_request_omitted_when_all_defaults():
    routes = [_make_route()]
    config = build_ingress(routes)
    assert "originRequest" not in config["ingress"][0]


def test_warp_routing_disabled():
    config = build_ingress([])
    assert config["warp-routing"] == {"enabled": False}
