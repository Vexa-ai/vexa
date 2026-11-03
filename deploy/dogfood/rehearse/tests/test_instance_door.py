"""`blank-admin` reads the instance state on admin-api's internal tier (`GET /internal/instance`).
`GET /admin/instance` is gone from the product, so a door still pointing there reads every stack as
unreadable and refuses."""
from __future__ import annotations

import pytest

import rehearse.doors as D
from rehearse.doors import DoorRefused, LiveDoors


class _Calls(list):
    answers: dict


@pytest.fixture
def calls(monkeypatch):
    seen = _Calls()
    answers = {"admin_exists": False}

    def fake_http(method, url, headers=None, body=None, timeout=40):
        seen.append((method, url, dict(headers or {})))
        if url.endswith("/internal/instance"):
            return 200, dict(answers)
        return 404, {"detail": "Not Found"}

    monkeypatch.setattr(D, "_http", fake_http)
    monkeypatch.setenv("INTERNAL_API_SECRET", "test-internal-secret")
    seen.answers = answers
    return seen


def _doors() -> LiveDoors:
    return LiveDoors(admin_key="test-admin-key", flows_key="test-flows-key")


def test_blank_reads_the_internal_instance_door(calls):
    assert _doors().require_instance_blank() == {"blank": True, "admin_exists": False}
    method, url, headers = calls[-1]
    assert (method, url) == ("GET", f"{D.ADMIN_API}/internal/instance")
    assert headers.get("X-Internal-Secret") == "test-internal-secret"
    assert "X-Admin-API-Key" not in headers, "the internal door takes the internal tier only"
    assert not [u for _, u, _ in calls if "/admin/instance" in u]


def test_a_claimed_instance_is_refused(calls):
    calls.answers["admin_exists"] = True
    with pytest.raises(DoorRefused, match="NOT blank"):
        _doors().require_instance_blank()
