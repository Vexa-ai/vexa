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


def test_post_meetings_with_a_non_iso_time_is_422():
    r = _client(InMemoryTranscriptStore()).post(
        "/meetings", headers={"X-User-Id": "7"},
        json={"meeting_url": "https://meet.google.com/abc-defg-hiy", "scheduled_at": "soon"})
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
