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

    async def sync_now(user_id: int, calendar_id=None):
        calls["now"] += 1
        calls["calendar_id"] = calendar_id
        return now_result

    async def sync_status(user_id: int, calendar_id=None):
        calls["status"] += 1
        calls["calendar_id"] = calendar_id
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


def test_connection_scoped_sync_routes_forward_calendar_id():
    stamp = {"last_sync": "2026-08-14T10:30:00+00:00", "last_error": None}
    client, calls = _client(now_result=stamp, status_result=stamp)
    r = client.get("/user/calendars/work-1/sync", headers={"X-User-Id": "28"})
    assert r.status_code == 200
    assert calls["calendar_id"] == "work-1"
    r = client.post("/user/calendars/work-1/sync", headers={"X-User-Id": "28"})
    assert r.status_code == 200
    assert calls["calendar_id"] == "work-1"


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

    text, err, _validators = asyncio.run(
        cal_adapters.fetch_ics("https://calendar.example.com/basic.ics"))
    if expect is None:
        assert err is None and text is not None
    else:
        assert text is None and expect in (err or "")


# ── the per-meeting auto-join opt-out survives the next sweep (route → store → sync) ──────────
def test_patching_auto_join_off_survives_the_next_calendar_sweep():
    """PATCH /meetings/{id} {"auto_join": false} is the user's decision about ONE meeting; the
    sweep recomputes the flag from the connected calendars' policy on every pass. The route marks
    the row as user-set so the sweep stands down on it — end to end, over the shipped handlers."""
    import asyncio
    from datetime import datetime, timezone

    from meeting_api.calendar_sync import parse_ics, sync_user

    store = InMemoryTranscriptStore()
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    feed = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
            "BEGIN:VEVENT\r\nUID:uid-1\r\nDTSTAMP:20260701T000000Z\r\nDTSTART:20260708T150000Z\r\n"
            "SUMMARY:Weekly sync\r\nLOCATION:https://meet.google.com/abc-defg-hij\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")
    asyncio.run(sync_user(store, 28, parse_ics(feed, now=now),
                          calendar_id="work", calendar_name="Work", auto_join_default=True))
    (row,) = asyncio.run(store.list_meetings(28))

    app = create_app(store, redis=_CaptureRedis())
    client = TestClient(app)
    r = client.patch(f"/meetings/{row['id']}", headers={"X-User-Id": "28"},
                     json={"auto_join": False})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["auto_join"] is False

    # the calendar was renamed upstream — a real source change, so the sweep does write the row
    asyncio.run(sync_user(store, 28, parse_ics(feed, now=now),
                          calendar_id="work", calendar_name="Work calendar",
                          auto_join_default=True))

    (row,) = asyncio.run(store.list_meetings(28))
    assert row["data"]["auto_join"] is False
    assert row["data"]["calendar_name"] == "Work calendar"


# ---- ACTIVE connections only — a tombstone is neither syncable nor listable ------------------
# Live 2026-08-17: a user whose calendar connections were ALL deleted got 200 from
# POST /user/calendar/sync plus a `calendars[]` array naming every deleted one. The documented
# answer is 404 (no active feed), and a roster the user already removed is not theirs to read back.

def _cfg(calendar_id, *, user_id=13820, **extra):
    return {"user_id": user_id, "calendar_id": calendar_id,
            "calendar_name": f"{calendar_id} calendar",
            "ics_url": f"https://cal.example/{calendar_id}.ics", **extra}


def test_active_configs_excludes_tombstones_and_other_users():
    from meeting_api.calendar_sync import active_configs

    configs = [
        _cfg("work"),
        _cfg("dead", deleted=True),
        _cfg("paused-but-mine", paused=True),
        _cfg("someone-elses", user_id=99),
    ]
    assert [c["calendar_id"] for c in active_configs(configs, 13820)] == [
        "work", "paused-but-mine",   # PAUSED is not a tombstone — the user still has it
    ]


def test_active_configs_empty_when_every_connection_is_deleted():
    """The exact staging shape → the sync-now hook returns None → the route answers 404."""
    from meeting_api.calendar_sync import active_configs

    configs = [_cfg("dead-1", deleted=True), _cfg("dead-2", deleted=True)]
    assert active_configs(configs, 13820) == []
    assert active_configs(configs, 13820, "dead-1") == []
    assert active_configs(None, 13820) == []


def test_active_configs_scopes_to_one_connection():
    from meeting_api.calendar_sync import active_configs

    configs = [_cfg("work"), _cfg("personal")]
    assert [c["calendar_id"] for c in active_configs(configs, 13820, "personal")] == ["personal"]
    assert active_configs(configs, 13820, "never-connected") == []


def test_no_response_ever_names_a_deleted_connection():
    """Whatever a stamp roster is built from, a tombstone is not in it."""
    from meeting_api.calendar_sync import active_configs, aggregate_stamps

    configs = [_cfg("work"), _cfg("dead", deleted=True)]
    stamps = [{"calendar_id": c["calendar_id"], "calendar_name": c["calendar_name"],
               "last_sync": "2026-08-17T20:06:00+00:00", "last_error": None,
               "counts": {"created": 0, "updated": 0, "cancelled": 0}}
              for c in active_configs(configs, 13820)]

    aggregate = aggregate_stamps(stamps)
    assert [s["calendar_id"] for s in aggregate["calendars"]] == ["work"]
    assert "dead" not in str(aggregate)


def test_unchanged_feed_transfers_no_body(monkeypatch):
    """The point of conditional fetching: an unchanged feed costs a header exchange.

    Without this, every sweep downloads every feed in full — at ten thousand feeds on a
    five-minute tick, 120k full transfers an hour, and the provider throttles us long before
    anyone connects the two facts.
    """
    import asyncio

    import httpx

    from meeting_api.calendar_sync import adapters as cal_adapters

    seen: list[dict] = []

    def fake_transport():
        def handler(request):
            seen.append(dict(request.headers))
            if request.headers.get("If-None-Match") == '"v1"':
                # a real 304 carries no body at all
                return httpx.Response(304)
            return httpx.Response(200, text="BEGIN:VCALENDAR\nEND:VCALENDAR",
                                  headers={"ETag": '"v1"'})
        return httpx.MockTransport(handler)

    import meeting_api.webhooks.ssrf as ssrf
    monkeypatch.setattr(ssrf, "build_pinned_transport", fake_transport)

    url = "https://calendar.example.com/basic.ics"

    # first poll: nothing known yet, so a full fetch — and the feed hands back its validator
    text, err, validators = asyncio.run(cal_adapters.fetch_ics(url))
    assert err is None and text is not None
    assert validators["etag"] == '"v1"'
    assert "if-none-match" not in seen[0]

    # second poll, carrying it back: no body, and the caller is told why
    text2, err2, v2 = asyncio.run(cal_adapters.fetch_ics(url, etag=validators["etag"]))
    assert seen[1]["if-none-match"] == '"v1"'
    assert v2.get("not_modified") is True
    assert text2 is None          # nothing transferred
    assert err2 is None           # and nothing wrong — unchanged is not a failure
