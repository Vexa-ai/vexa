"""ADR-0052 §3, Q1 (a) (decided 4 Nov 2026): where no scheduler is configured — meeting-api's
``VEXA_FLOWS_API_URL`` publish edge unset — a booking with ``auto_join`` on is refused by name
instead of being accepted and never served. ``POST /meetings`` (and a ``PATCH`` or ``PUT …/intent``
that makes a plan a booking) answers ``422`` with ``code: scheduling_not_deployed`` and the ``key``
to set; calendar sync still mirrors the event, with ``auto_join`` off and ``auto_join_error``
"scheduling not deployed". A plan with ``auto_join`` off is unaffected, and so is everything that
is not a booking.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
H = {"X-User-Id": str(USER)}
REFUSED = {"code": "scheduling_not_deployed", "key": "VEXA_FLOWS_API_URL"}


def _future(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


@pytest.fixture
def no_scheduler(monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)


def _client(store=None):
    store = store or InMemoryTranscriptStore()
    return TestClient(create_app(transcript_store=store)), store


def _refused(r):
    assert r.status_code == 422, r.text
    body = r.json()
    assert {k: body.get(k) for k in REFUSED} == REFUSED
    assert isinstance(body["detail"], str) and "VEXA_FLOWS_API_URL" in body["detail"]
    assert body["detail"].startswith("scheduling not deployed")


def test_post_meetings_with_auto_join_is_refused_by_name(no_scheduler):
    c, store = _client()
    for body in ({"meeting_url": "https://meet.google.com/abc-defg-hij", "scheduled_at": _future(60)},
                 {"meeting_url": "https://meet.google.com/abc-defg-hik", "scheduled_at": _future(60),
                  "auto_join": True}):
        _refused(c.post("/meetings", headers=H, json=body))
    assert c.get("/meetings", headers=H).json()["meetings"] == [], "a refused booking was written"


def test_a_blank_address_is_no_scheduler(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "   ")
    c, _ = _client()
    _refused(c.post("/meetings", headers=H, json={"meeting_url": "https://meet.google.com/abc-defg-hij",
                                                  "scheduled_at": _future(60)}))


def test_a_plan_without_a_bot_is_unaffected(no_scheduler):
    c, _ = _client()
    off = c.post("/meetings", headers=H, json={"meeting_url": "https://meet.google.com/abc-defg-hij",
                                               "scheduled_at": _future(60), "auto_join": False})
    assert off.status_code == 201, off.text
    assert off.json()["status"] == "scheduled" and off.json()["data"]["auto_join"] is False
    idle = c.post("/meetings", headers=H, json={"title": "no time yet"})
    assert idle.status_code == 201 and idle.json()["status"] == "idle"


def test_with_a_scheduler_the_booking_is_accepted(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://flows-api:8200")
    c, _ = _client()
    r = c.post("/meetings", headers=H, json={"meeting_url": "https://meet.google.com/abc-defg-hij",
                                             "scheduled_at": _future(60)})
    assert r.status_code == 201, r.text
    assert r.json()["data"]["auto_join"] is True and "auto_join_error" not in r.json()["data"]


def test_patch_that_makes_a_booking_is_refused_and_writes_nothing(no_scheduler):
    c, store = _client()
    off = c.post("/meetings", headers=H, json={"meeting_url": "https://meet.google.com/abc-defg-hij",
                                               "scheduled_at": _future(60), "auto_join": False}).json()
    _refused(c.patch(f"/meetings/{off['id']}", headers=H, json={"auto_join": True}))
    idle = c.post("/meetings", headers=H,
                  json={"meeting_url": "https://meet.google.com/abc-defg-hik"}).json()
    _refused(c.patch(f"/meetings/{idle['id']}", headers=H, json={"scheduled_at": _future(90)}))
    _refused(c.patch("/meetings/google_meet/abc-defg-hik", headers=H,
                     json={"scheduled_at": _future(90)}))
    rows = {m["id"]: m for m in c.get("/meetings", headers=H).json()["meetings"]}
    assert rows[off["id"]]["data"]["auto_join"] is False
    assert rows[idle["id"]]["status"] == "idle"
    # an edit that makes no booking is never refused
    assert c.patch(f"/meetings/{off['id']}", headers=H, json={"title": "renamed"}).status_code == 200


def test_put_intent_that_makes_a_booking_is_refused(no_scheduler):
    c, _ = _client()
    assert c.post("/meetings", headers=H,
                  json={"meeting_url": "https://meet.google.com/abc-defg-hij"}).status_code == 201
    _refused(c.put("/meetings/google_meet/abc-defg-hij/intent", headers=H,
                   json={"intent": "scheduled", "at": _future(60)}))


async def test_calendar_sync_mirrors_the_event_with_no_bot_and_the_reason(no_scheduler):
    from meeting_api.calendar_sync import parse_ics, sync_user

    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=3)).strftime("%Y%m%dT%H%M%SZ")
    feed = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\nUID:sched-1\r\n"
            f"DTSTAMP:20261101T000000Z\r\nDTSTART:{start}\r\nSUMMARY:Sync\r\n"
            "LOCATION:https://meet.google.com/cal-schd-one\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(feed, now=now), calendar_id="work",
                    calendar_name="Work", bot_name="Bot A")
    row = next(m for m in await store.list_meetings(USER)
               if m["native_meeting_id"] == "cal-schd-one")
    assert row["status"] == "scheduled", "the calendar's event is still mirrored"
    assert row["data"]["auto_join"] is False
    assert row["data"]["auto_join_error"] == "scheduling not deployed"
    # the source changes (another bot name): calendar sync re-derives auto_join, and it stays off
    await sync_user(store, USER, parse_ics(feed, now=now), calendar_id="work",
                    calendar_name="Work", bot_name="Bot B")
    row = next(m for m in await store.list_meetings(USER)
               if m["native_meeting_id"] == "cal-schd-one")
    assert row["data"]["auto_join"] is False
    assert row["data"]["auto_join_error"] == "scheduling not deployed"


async def test_a_row_armed_again_once_a_scheduler_is_configured_drops_the_stamp(monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)
    store = InMemoryTranscriptStore()
    row = await store.create_planned_meeting(USER, platform="google_meet",
                                             native_meeting_id="cal-schd-two",
                                             scheduled_at=_future(60), over_cap="stamp")
    assert row["data"]["auto_join_error"] == "scheduling not deployed"
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://flows-api:8200")
    again = await store.update_planned_meeting(USER, row["id"], {"auto_join": True})
    assert again["data"]["auto_join"] is True and "auto_join_error" not in again["data"]
