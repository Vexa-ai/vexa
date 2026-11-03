"""ADR-0052 s2c — the one creation path of a booked row: ISO check and the booking cap.

``create_planned_meeting`` (both repos) refuses a ``scheduled_at`` that is not ISO-8601 and counts
the account's future scheduled rows with auto-join on against ``BOOKED_MAX_PER_ACCOUNT``. Over it
``POST /meetings`` answers 429 with the cap; calendar sync still mirrors the event, with no bot and
the reason on the row. Also: the concurrency ceiling counts ``needs_help`` / ``needs_human_help``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7


def _future(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


async def _book(store, n, **kw):
    return await store.create_planned_meeting(
        USER, platform="google_meet", native_meeting_id=f"aaa-bbbb-{n:03d}",
        scheduled_at=kw.pop("scheduled_at", _future(60 + n)), **kw)


@pytest.fixture(autouse=True)
def _cap(monkeypatch):
    monkeypatch.setenv("BOOKED_MAX_PER_ACCOUNT", "3")


async def test_the_cap_refuses_the_next_booking():
    store = InMemoryTranscriptStore()
    for n in range(3):
        assert "error" not in await _book(store, n)
    assert await _book(store, 3) == {"error": "booking_cap", "cap": 3}


async def test_only_future_bookings_with_a_bot_count():
    store = InMemoryTranscriptStore()
    await _book(store, 0, auto_join=False)                         # no bot: not counted
    await _book(store, 1, scheduled_at=_future(-30))               # past: not counted
    await _book(store, 2)
    await _book(store, 3)
    assert "error" not in await _book(store, 4)
    assert (await _book(store, 5))["error"] == "booking_cap"


async def test_a_plan_without_a_bot_is_never_capped():
    store = InMemoryTranscriptStore()
    for n in range(3):
        await _book(store, n)
    assert "error" not in await _book(store, 9, auto_join=False)
    assert "error" not in await store.create_planned_meeting(
        USER, platform="google_meet", native_meeting_id="idl-eeee-eee")   # no time: idle


async def test_calendar_sync_over_the_cap_mirrors_the_event_with_no_bot():
    store = InMemoryTranscriptStore()
    for n in range(3):
        await _book(store, n)
    row = await _book(store, 3, over_cap="stamp")
    assert "error" not in row and row["status"] == "scheduled"
    assert row["data"]["auto_join"] is False
    assert row["data"]["auto_join_error"].startswith("booking cap reached")


# One cap for the whole deployment (founder, 3 Nov 2026): BOOKED_MAX_PER_ACCOUNT, 500 when unset,
# set through compose, Helm (meetingApi.bookedMaxPerAccount) or Lite.
async def test_unset_the_cap_is_500(monkeypatch):
    monkeypatch.delenv("BOOKED_MAX_PER_ACCOUNT")
    store = InMemoryTranscriptStore()
    for n in range(500):
        assert "error" not in await _book(store, n)
    assert await _book(store, 500) == {"error": "booking_cap", "cap": 500}


@pytest.mark.parametrize("cap", [600, 10], ids=["raised", "lowered"])
async def test_the_deployment_setting_raises_and_lowers_the_cap(monkeypatch, cap):
    monkeypatch.setenv("BOOKED_MAX_PER_ACCOUNT", str(cap))
    store = InMemoryTranscriptStore()
    for n in range(cap):                         # 600: the 501st and on are booked
        assert "error" not in await _book(store, n)
    assert await _book(store, cap) == {"error": "booking_cap", "cap": cap}


@pytest.mark.parametrize("bad", ["abc", "-1", "1e+06", "2.5", "12x"])
def test_a_cap_that_is_not_a_whole_number_refuses_the_boot(bad):
    """It used to become 500 with no word: a cap nobody chose (Factory review of #1998 at 4ef91713)."""
    from meeting_api.__main__ import _booking_cap_at_boot
    from meeting_api.config_preflight import ConfigError

    with pytest.raises(ConfigError, match="refuses to boot: BOOKED_MAX_PER_ACCOUNT must be a whole"):
        _booking_cap_at_boot({"BOOKED_MAX_PER_ACCOUNT": bad})


@pytest.mark.parametrize("env,cap", [({}, 500), ({"BOOKED_MAX_PER_ACCOUNT": ""}, 500),
                                     ({"BOOKED_MAX_PER_ACCOUNT": "0"}, 0),
                                     ({"BOOKED_MAX_PER_ACCOUNT": " 1000000 "}, 1000000)])
def test_a_whole_number_or_nothing_boots(env, cap):
    from meeting_api.__main__ import _booking_cap_at_boot

    assert _booking_cap_at_boot(env) == cap


@pytest.mark.parametrize("bad", ["tomorrow", "2026-13-01T10:00:00Z", "10:00"])
async def test_a_non_iso_time_is_refused(bad):
    store = InMemoryTranscriptStore()
    assert await _book(store, 0, scheduled_at=bad) == {"error": "invalid_scheduled_at"}


def _client(store):
    from meeting_api import create_app

    return TestClient(create_app(transcript_store=store))


def test_post_meetings_over_the_cap_is_429_with_the_cap():
    store = InMemoryTranscriptStore()
    c = _client(store)
    for n in range(3):
        r = c.post("/meetings", headers={"X-User-Id": "7"},
                   json={"meeting_url": f"https://meet.google.com/abc-defg-hi{chr(97 + n)}",
                         "scheduled_at": _future(60 + n)})
        assert r.status_code == 201, r.text
    r = c.post("/meetings", headers={"X-User-Id": "7"},
               json={"meeting_url": "https://meet.google.com/abc-defg-hiz",
                     "scheduled_at": _future(90)})
    assert r.status_code == 429 and r.json()["cap"] == 3


def test_the_429_reports_the_deployment_s_cap(monkeypatch):
    monkeypatch.setenv("BOOKED_MAX_PER_ACCOUNT", "10")
    c = _client(InMemoryTranscriptStore())
    for n in range(10):
        r = c.post("/meetings", headers={"X-User-Id": "7"},
                   json={"meeting_url": f"https://meet.google.com/abc-defg-hi{chr(97 + n)}",
                         "scheduled_at": _future(60 + n)})
        assert r.status_code == 201, r.text
    r = c.post("/meetings", headers={"X-User-Id": "7"},
               json={"meeting_url": "https://meet.google.com/abc-defg-hiz",
                     "scheduled_at": _future(90)})
    assert r.status_code == 429 and r.json()["cap"] == 10
    assert "already has 10 future meetings" in r.json()["detail"]


def test_post_meetings_with_a_non_iso_time_is_422():
    r = _client(InMemoryTranscriptStore()).post(
        "/meetings", headers={"X-User-Id": "7"},
        json={"meeting_url": "https://meet.google.com/abc-defg-hiy", "scheduled_at": "soon"})
    assert r.status_code == 422


# ── every writer that can make a row a booking is capped, not only the creation path ────────────
# (review of #1998 @ 5898f34a: idle plans ×N, then PATCH scheduled_at, armed >500 rows uncapped)

async def _full(store):
    for n in range(3):
        assert "error" not in await _book(store, n)


async def test_patch_giving_an_idle_plan_a_time_is_capped_and_writes_nothing():
    store = InMemoryTranscriptStore()
    await _full(store)
    idle = await store.create_planned_meeting(USER, platform="google_meet",
                                              native_meeting_id="idl-aaaa-aaa")
    assert idle["status"] == "idle"
    out = await store.update_planned_meeting(USER, idle["id"], {"scheduled_at": _future(120),
                                                                "title": "x"})
    assert out == {"error": "booking_cap", "cap": 3}
    row = next(m for m in await store.list_meetings(USER) if m["id"] == idle["id"])
    assert row["status"] == "idle" and "scheduled_at" not in row["data"] and "title" not in row["data"]


async def test_patch_turning_auto_join_back_on_is_capped():
    store = InMemoryTranscriptStore()
    off = await _book(store, 9, auto_join=False)
    await _full(store)
    assert await store.update_planned_meeting(USER, off["id"], {"auto_join": True}) == {
        "error": "booking_cap", "cap": 3}


async def test_patch_moving_a_past_plan_ahead_is_capped():
    store = InMemoryTranscriptStore()
    past = await _book(store, 9, scheduled_at=_future(-30))
    await _full(store)
    assert (await store.update_planned_meeting(
        USER, past["id"], {"scheduled_at": _future(200)}))["error"] == "booking_cap"


async def test_a_booking_that_already_counts_moves_and_edits_at_the_cap():
    store = InMemoryTranscriptStore()
    await _full(store)
    row = (await store.list_meetings(USER))[0]
    out = await store.update_planned_meeting(USER, row["id"], {"scheduled_at": _future(300),
                                                               "auto_join": True, "title": "t"})
    assert "error" not in out and out["data"]["auto_join"] is True


async def test_a_non_iso_time_is_refused_on_patch():
    store = InMemoryTranscriptStore()
    row = await _book(store, 0)
    assert await store.update_planned_meeting(USER, row["id"], {"scheduled_at": "not-a-time"}) == {
        "error": "invalid_scheduled_at"}
    assert (await store.list_meetings(USER))[0]["data"]["scheduled_at"] != "not-a-time"


async def test_set_intent_scheduling_a_plan_is_capped():
    store = InMemoryTranscriptStore()
    await _full(store)
    await store.create_planned_meeting(USER, platform="google_meet", native_meeting_id="idl-bbbb-bbb")
    out = await store.set_intent(USER, "google_meet", "idl-bbbb-bbb", "scheduled",
                                 scheduled_at=_future(120))
    assert out == {"error": "booking_cap", "cap": 3}
    assert await store.set_intent(USER, "google_meet", "idl-bbbb-bbb", "scheduled",
                                  scheduled_at="soon") == {"error": "invalid_scheduled_at"}
    row = next(m for m in await store.list_meetings(USER) if m["native_meeting_id"] == "idl-bbbb-bbb")
    assert row["status"] == "idle"


async def test_calendar_sync_update_over_the_cap_mirrors_with_no_bot_then_rearms_under_it():
    store = InMemoryTranscriptStore()
    off = await _book(store, 9, auto_join=False)
    await _full(store)
    out = await store.update_planned_meeting(USER, off["id"], {"auto_join": True,
                                                               "scheduled_at": _future(400)},
                                             over_cap="stamp")
    assert out["data"]["auto_join"] is False
    assert out["data"]["auto_join_error"].startswith("booking cap reached")
    assert out["data"]["scheduled_at"], "the calendar's change is still mirrored"
    assert await store.delete_planned_meeting(USER, next(
        m["id"] for m in await store.list_meetings(USER)
        if m["id"] != off["id"] and m["data"].get("auto_join"))) is True
    again = await store.update_planned_meeting(USER, off["id"], {"auto_join": True},
                                               over_cap="stamp")
    assert again["data"]["auto_join"] is True and "auto_join_error" not in again["data"]


async def test_calendar_sync_cannot_rearm_a_row_over_the_cap():
    """The review's recipe: a calendar row created over the cap (no bot) is re-derived
    ``auto_join=True`` by ``_source_updates`` when its source changes; it must stay without a bot."""
    from meeting_api.calendar_sync import parse_ics, sync_user

    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=3)).strftime("%Y%m%dT%H%M%SZ")
    feed = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\nUID:cap-1\r\n"
            f"DTSTAMP:20261101T000000Z\r\nDTSTART:{start}\r\nSUMMARY:Sync\r\n"
            "LOCATION:https://meet.google.com/cal-capp-one\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    store = InMemoryTranscriptStore()
    await _full(store)
    await sync_user(store, USER, parse_ics(feed, now=now), calendar_id="work",
                    calendar_name="Work", bot_name="Bot A")
    row = next(m for m in await store.list_meetings(USER)
               if m["native_meeting_id"] == "cal-capp-one")
    assert row["data"]["auto_join"] is False and row["data"]["auto_join_error"]
    # the source entry changes (another bot name) → sync re-derives auto_join from the calendars
    await sync_user(store, USER, parse_ics(feed, now=now), calendar_id="work",
                    calendar_name="Work", bot_name="Bot B")
    row = next(m for m in await store.list_meetings(USER)
               if m["native_meeting_id"] == "cal-capp-one")
    assert row["data"]["auto_join"] is False, "calendar sync re-armed a row over the cap"
    assert row["data"]["auto_join_error"].startswith("booking cap reached")


def test_patch_over_the_cap_is_429_with_the_cap_and_non_iso_is_422():
    store = InMemoryTranscriptStore()
    c = _client(store)
    for n in range(3):
        assert c.post("/meetings", headers={"X-User-Id": "7"},
                      json={"meeting_url": f"https://meet.google.com/abc-defg-hi{chr(97 + n)}",
                            "scheduled_at": _future(60 + n)}).status_code == 201
    idle = c.post("/meetings", headers={"X-User-Id": "7"},
                  json={"meeting_url": "https://meet.google.com/abc-defg-hiz"})
    assert idle.status_code == 201 and idle.json()["status"] == "idle"
    mid = idle.json()["id"]
    r = c.patch(f"/meetings/{mid}", headers={"X-User-Id": "7"}, json={"scheduled_at": _future(90)})
    assert r.status_code == 429 and r.json()["cap"] == 3, r.text
    r = c.patch("/meetings/google_meet/abc-defg-hiz", headers={"X-User-Id": "7"},
                json={"scheduled_at": _future(90)})
    assert r.status_code == 429 and r.json()["cap"] == 3, r.text
    r = c.patch(f"/meetings/{mid}", headers={"X-User-Id": "7"}, json={"scheduled_at": "not-a-time"})
    assert r.status_code == 422


def test_put_intent_over_the_cap_is_429_and_non_iso_is_422():
    store = InMemoryTranscriptStore()
    c = _client(store)
    for n in range(3):
        assert c.post("/meetings", headers={"X-User-Id": "7"},
                      json={"meeting_url": f"https://meet.google.com/abc-defg-hi{chr(97 + n)}",
                            "scheduled_at": _future(60 + n)}).status_code == 201
    assert c.post("/meetings", headers={"X-User-Id": "7"},
                  json={"meeting_url": "https://meet.google.com/abc-defg-hiz"}).status_code == 201
    r = c.put("/meetings/google_meet/abc-defg-hiz/intent", headers={"X-User-Id": "7"},
              json={"intent": "scheduled", "at": _future(90)})
    assert r.status_code == 429 and r.json()["cap"] == 3, r.text
    r = c.put("/meetings/google_meet/abc-defg-hiz/intent", headers={"X-User-Id": "7"},
              json={"intent": "scheduled", "at": "tomorrow"})
    assert r.status_code == 422


# ── the ceiling counts every pod that holds a slot ──────────────────────────────────────────────

@pytest.mark.parametrize("status,counted", [
    ("needs_help", True), ("needs_human_help", True), ("active", True), ("stopping", False),
])
async def test_the_ceiling_counts_needs_help_and_not_stopping(status, counted):
    from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo

    repo = InMemoryMeetingRepo()
    repo._meetings[1] = {"id": 1, "user_id": USER, "platform": "google_meet",
                         "native_meeting_id": "x", "platform_specific_id": "x", "status": status,
                         "data": {}}
    assert await repo.count_active_bots(user_id=USER) == (1 if counted else 0)
