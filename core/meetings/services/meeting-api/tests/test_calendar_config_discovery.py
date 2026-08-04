"""#991 — admin-api calendar-config discovery keeps faults distinct from absence.

The internal edge has three outcomes: a validated config list (possibly empty), a matching
gateway-bound user's config (or authoritative absence), and a typed retryable discovery fault.
Secret URLs, internal credentials, and response bodies never enter the exception surface.
"""
from __future__ import annotations

import httpx
import pytest

from meeting_api.calendar_sync import (
    CalendarConfigDiscoveryError,
    CalendarConfigDiscoveryKind,
    fetch_configs,
    fetch_user_config,
)

ADMIN = "http://admin-api:8001"
SECRET = "do-not-log-this-secret"


def _mock_http(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)


async def test_authoritative_empty_is_a_successful_list(monkeypatch):
    _mock_http(monkeypatch, lambda request: httpx.Response(200, json={"configs": []}))

    assert await fetch_configs(ADMIN, SECRET) == []
    assert await fetch_user_config(ADMIN, SECRET, 28) is None


async def test_saved_config_discovery_selects_only_the_bound_user(monkeypatch):
    configs = [
        {"user_id": 11, "ics_url": "https://calendar.example/a.ics", "auto_join": True},
        {"user_id": 28, "ics_url": "https://calendar.example/b.ics", "auto_join": False},
    ]
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["secret"] = request.headers.get("X-Internal-Secret")
        return httpx.Response(200, json={"configs": configs})

    _mock_http(monkeypatch, handler)

    found = await fetch_user_config(ADMIN, SECRET, 28)
    assert found == configs[1]
    assert seen == {"path": "/internal/calendar-configs", "secret": SECRET}


@pytest.mark.parametrize(
    ("admin_api_url", "internal_secret"),
    [
        ("", SECRET),
        ("http://", SECRET),
        ("ftp://admin-api:8001", SECRET),
        (ADMIN, ""),
    ],
)
async def test_invalid_discovery_configuration_is_typed_and_sanitized(
    admin_api_url, internal_secret,
):
    with pytest.raises(CalendarConfigDiscoveryError) as caught:
        await fetch_configs(admin_api_url, internal_secret)

    assert caught.value.kind is CalendarConfigDiscoveryKind.CONFIGURATION
    assert SECRET not in str(caught.value)
    if admin_api_url:
        assert admin_api_url not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (401, {"detail": "rejected"}, "authentication"),
        (403, {"detail": "rejected"}, "authentication"),
        (500, {"detail": "database unavailable"}, "upstream_status"),
        (200, {"wrong": []}, "response_shape"),
        (200, {"configs": [{"user_id": 28}]}, "response_shape"),
        (200, {"configs": [
            {"user_id": 28, "ics_url": "https://calendar.example/a.ics"},
        ]}, "response_shape"),
        (200, {"configs": [
            {"user_id": True, "ics_url": "https://calendar.example/a.ics", "auto_join": True},
        ]}, "response_shape"),
        (200, {"configs": [
            {"user_id": 28, "ics_url": "https://calendar.example/a.ics", "auto_join": True},
            {"user_id": 28, "ics_url": "https://calendar.example/b.ics", "auto_join": False},
        ]}, "response_shape"),
    ],
)
async def test_upstream_faults_are_typed_and_sanitized(monkeypatch, status, body, kind):
    _mock_http(monkeypatch, lambda request: httpx.Response(status, json=body))

    with pytest.raises(CalendarConfigDiscoveryError) as caught:
        await fetch_configs(ADMIN, SECRET)

    assert caught.value.kind is CalendarConfigDiscoveryKind(kind)
    assert SECRET not in str(caught.value)
    assert "calendar.example" not in str(caught.value)
    assert "database unavailable" not in str(caught.value)


async def test_invalid_json_is_a_typed_response_shape_fault(monkeypatch):
    _mock_http(monkeypatch, lambda request: httpx.Response(200, text="not-json"))

    with pytest.raises(CalendarConfigDiscoveryError, match="response_shape"):
        await fetch_configs(ADMIN, SECRET)


@pytest.mark.parametrize(
    ("error_type", "kind"),
    [
        (httpx.ReadTimeout, "transport"),
        (httpx.ConnectError, "connectivity"),
    ],
)
async def test_transport_faults_are_typed_and_sanitized(monkeypatch, error_type, kind):
    def handler(request):
        raise error_type("sensitive network detail", request=request)

    _mock_http(monkeypatch, handler)

    with pytest.raises(CalendarConfigDiscoveryError) as caught:
        await fetch_configs(ADMIN, SECRET)

    assert caught.value.kind is CalendarConfigDiscoveryKind(kind)
    assert "sensitive network detail" not in str(caught.value)
    assert SECRET not in str(caught.value)
