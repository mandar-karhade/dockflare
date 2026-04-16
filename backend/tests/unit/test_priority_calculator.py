"""Unit tests for priority calculator."""

from __future__ import annotations

import pytest

from app.services.priority_calculator import calculate_priority


@pytest.mark.parametrize(
    "hostname,path_regex,expected_range",
    [
        ("app.example.com", None, (550, 750)),
        ("*.example.com", None, (800, 1000)),
        ("api.v2.example.com", "^/v2/.*", (1, 350)),
        ("example.com", None, (650, 800)),
    ],
)
def test_priority_calculation(
    hostname: str,
    path_regex: str | None,
    expected_range: tuple[int, int],
):
    result = calculate_priority(hostname, path_regex)
    assert expected_range[0] <= result <= expected_range[1], (
        f"Priority {result} not in range {expected_range} for {hostname} path={path_regex}"
    )


def test_more_specific_hostname_gets_lower_priority():
    """Longer hostnames match before shorter ones."""
    specific = calculate_priority("api.v2.staging.example.com", None)
    general = calculate_priority("example.com", None)
    assert specific < general


def test_exact_beats_wildcard():
    exact = calculate_priority("app.example.com", None)
    wildcard = calculate_priority("*.example.com", None)
    assert exact < wildcard


def test_path_regex_beats_no_path():
    with_path = calculate_priority("app.example.com", "^/api/.*")
    without_path = calculate_priority("app.example.com", None)
    assert with_path < without_path
