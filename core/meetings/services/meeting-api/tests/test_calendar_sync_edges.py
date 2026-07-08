"""User-facing calendar-sync edges (GET/POST /user/calendar/sync) — the fail-loud feedback loop.

Offline over the collector's standalone ``create_app`` with the hooks injected as plain fakes
(the composition root wires the real ones; the route contract is what's under test):
  * GET  → last stamp verbatim, {} before any sync, 503 when unwired, 401 without identity.
  * POST → runs the hook and returns the fresh stamp; 404 when the user has no feed connected;
           503 when unwired.
Plus the fetch layer's user-facing error taxonomy (HTML page / redirect / non-ICS content) —
the exact strings a lost user sees in the panel, so they must name the fix, not the failure.
"""
import pytest
from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore


class _CaptureRedis:
    async def publish(self, channel, data):
        pass


def _client(now_result=None, status_result=None, wired=True):
    calls = {"now": 0, "status": 0}

    async def sync_now(user_id: int):
        calls["now"] += 1
        return now_result

    async def sync_status(user_id: int):
        calls["status"] += 1
        return status_result

    app = create_app(
        InMemoryTranscriptStore(), redis=_CaptureRedis(),
        calendar_sync_now=sync_now if wired else None,
        calendar_sync_status=sync_status if wired else None,
    )
    return TestClient(app), calls


def test_get_sync_status_returns_stamp():
    stamp = {"last_sync": "2026-07-08T14:41:26+00:00", "last_error": "boom"}
    client, calls = _client(status_result=stamp)
    r = client.get("/user/calendar/sync", headers={"X-User-Id": "28"})
    assert r.status_code == 200
    assert r.json() == stamp
    assert calls["status"] == 1


def test_get_sync_status_empty_before_first_run():
    client, _ = _client(status_result=None)
    r = client.get("/user/calendar/sync", headers={"X-User-Id": "28"})
    assert r.status_code == 200
    assert r.json() == {}


def test_post_sync_now_returns_fresh_stamp():
    stamp = {"last_sync": "2026-07-08T15:00:00+00:00", "last_error": None,
             "counts": {"created": 3, "updated": 0, "cancelled": 0}}
    client, calls = _client(now_result=stamp)
    r = client.post("/user/calendar/sync", headers={"X-User-Id": "28"})
    assert r.status_code == 200
    assert r.json()["counts"]["created"] == 3
    assert calls["now"] == 1


def test_post_sync_now_404_when_no_feed():
    client, _ = _client(now_result=None)
    r = client.post("/user/calendar/sync", headers={"X-User-Id": "28"})
    assert r.status_code == 404


def test_unwired_hooks_503():
    client, _ = _client(wired=False)
    assert client.get("/user/calendar/sync", headers={"X-User-Id": "1"}).status_code == 503
    assert client.post("/user/calendar/sync", headers={"X-User-Id": "1"}).status_code == 503


def test_identity_required():
    client, _ = _client(status_result={})
    assert client.get("/user/calendar/sync").status_code == 401
    assert client.post("/user/calendar/sync").status_code == 401


# ── fetch-layer error taxonomy (async, no server: httpx MockTransport is overkill here —
#    we monkeypatch the pinned transport with a handler-backed one) ────────────────────────
@pytest.mark.parametrize("body,status,expect", [
    ("<html><body>calendar</body></html>", 200, "web page"),
    ("BEGIN:VCALENDAR\nEND:VCALENDAR", 200, None),
    ("not a calendar at all", 200, "BEGIN:VCALENDAR"),
    ("", 404, "HTTP 404"),
])
def test_fetch_ics_error_taxonomy(monkeypatch, body, status, expect):
    import asyncio

    import httpx

    from meeting_api.calendar_sync import adapters as cal_adapters

    def fake_transport():
        def handler(request):
            return httpx.Response(status, text=body)
        return httpx.MockTransport(handler)

    import meeting_api.webhooks.ssrf as ssrf
    monkeypatch.setattr(ssrf, "build_pinned_transport", fake_transport)

    text, err = asyncio.run(cal_adapters.fetch_ics("https://calendar.example.com/basic.ics"))
    if expect is None:
        assert err is None and text is not None
    else:
        assert text is None and expect in (err or "")
