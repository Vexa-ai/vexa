"""#1222 — the meetings list orders by MEETING EVENT time, non-terminal rows pinned first.

A calendar-managed row is created at IMPORT time — possibly days before the meeting — so the old
`created_at DESC` list buried a meeting that was live RIGHT NOW under every row created since the
import. Witnessed in production 2026-08-18 (row 26298: imported Aug 16 22:40, scheduled Aug 18
09:00, sat at list position 19 while the founder was in the meeting).

The list-view sort is now, in order:
  1. non-terminal rows (scheduled/requested/joining/awaiting_admission/active/stopping) pin ABOVE
     terminal ones;
  2. within each group, event time DESC — COALESCE(data.scheduled_at, start_time, created_at);
  3. id DESC as a stable tiebreak.

Drives the SHIPPED handlers over the in-memory fake (TestClient, offline). The fake mirrors the
real ``SqlAlchemyTranscriptStore`` through the shared ``projection.list_order_key`` /
``LIST_PIN_STATUSES`` (the real store's SQL — ``meeting_event_time()`` + the status-pin expression
— is the same key, see collector/adapters.py + MIGRATION-0005), so the ordering proven here is the
shipped semantics. Internal enumeration (non-list_view) keeps created_at DESC — pinned separately.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher

USER = 7
HEADERS = {"x-user-id": str(USER)}


def _client(store):
    return TestClient(create_app(
        transcript_store=store,
        meeting_repo=InMemoryMeetingRepo(),
        runtime=FakeRuntimeClient(),
        command_publisher=InMemoryCommandPublisher(),
    ))


def _seed(store, nid, *, status, created_at, scheduled_at=None, start_time=None):
    data = {"scheduled_at": scheduled_at} if scheduled_at else {}
    return store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id=nid, status=status,
        created_at=created_at, start_time=start_time, data=data,
    )


def _ids(response):
    return [m["id"] for m in response.json()["meetings"]]


# ── C1 · the witnessed failure: imported-days-ago, live-now → position 0 ──────────────────────────

@pytest.mark.parametrize("path", ["/bots", "/meetings"])
def test_live_calendar_meeting_leads_the_list(path):
    """The prod-26298 shape: a calendar row imported on the 16th, live on the 18th, with a pile of
    rows created SINCE the import. Under created_at DESC it sat at position 19; it must be row 0."""
    store = InMemoryTranscriptStore()
    live = _seed(store, "live-now", status="active",
                 created_at="2026-08-16T22:40:00Z",         # calendar-import time
                 scheduled_at="2026-08-18T09:00:00Z",       # the actual meeting event
                 start_time="2026-08-18T09:01:00Z")
    for i in range(18):                                     # everything created since the import
        _seed(store, f"since-{i:02d}", status="completed",
              created_at=f"2026-08-17T{i:02d}:00:00Z",
              start_time=f"2026-08-17T{i:02d}:00:30Z")
    r = _client(store).get(path, headers=HEADERS)
    assert r.status_code == 200
    assert _ids(r)[0] == live, "the live calendar meeting must lead the list"


# ── C2 · every non-terminal status pins above every terminal row ──────────────────────────────────

def test_non_terminal_pins_above_terminal():
    """A terminal row with the NEWEST created_at and the latest event time still ranks below every
    non-terminal row — the pin outranks recency."""
    store = InMemoryTranscriptStore()
    fresh_terminal = _seed(store, "fresh-done", status="completed",
                           created_at="2026-08-18T12:00:00Z",
                           start_time="2026-08-18T12:00:30Z")
    pinned = [
        _seed(store, f"pin-{s}", status=s, created_at="2026-08-10T00:00:00Z",
              scheduled_at=f"2026-08-1{i}T09:00:00Z")
        for i, s in enumerate(
            ["scheduled", "requested", "joining", "awaiting_admission", "active", "stopping"])
    ]
    ids = _ids(_client(store).get("/bots", headers=HEADERS))
    assert ids[-1] == fresh_terminal, "terminal row must sink below all non-terminal rows"
    assert set(ids[:-1]) == set(pinned)


def test_within_groups_event_time_desc_id_tiebreak():
    """Inside the pinned group and inside the terminal group alike: event time DESC, where event
    time is COALESCE(scheduled_at, start_time, created_at); equal keys break newest-id-first."""
    store = InMemoryTranscriptStore()
    # pinned group: scheduled_at decides regardless of created_at
    pin_late = _seed(store, "pin-late", status="scheduled",
                     created_at="2026-08-01T00:00:00Z", scheduled_at="2026-08-19T09:00:00Z")
    pin_early = _seed(store, "pin-early", status="active",
                      created_at="2026-08-18T00:00:00Z", scheduled_at="2026-08-18T09:00:00Z")
    # terminal group: no scheduled_at → start_time; no start_time → created_at
    t_start = _seed(store, "t-start", status="completed",
                    created_at="2026-08-10T00:00:00Z", start_time="2026-08-17T10:00:00Z")
    t_created = _seed(store, "t-created", status="failed",
                      created_at="2026-08-16T00:00:00Z", start_time=None)
    # equal event time → higher id first
    tie_a = _seed(store, "tie-a", status="completed",
                  created_at="2026-08-15T00:00:00Z", start_time="2026-08-15T00:00:00Z")
    tie_b = _seed(store, "tie-b", status="completed",
                  created_at="2026-08-15T00:00:00Z", start_time="2026-08-15T00:00:00Z")
    ids = _ids(_client(store).get("/bots", headers=HEADERS))
    assert ids == [pin_late, pin_early, t_start, t_created, max(tie_a, tie_b), min(tie_a, tie_b)]


def test_offset_timestamps_compare_correctly():
    """scheduled_at arrives as ISO-8601 with arbitrary offsets (calendar sync); +02:00 vs Z must
    compare on the instant, not the wall-clock string."""
    store = InMemoryTranscriptStore()
    later = _seed(store, "z", status="completed",
                  created_at="2026-08-01T00:00:00Z", scheduled_at="2026-08-18T10:00:00Z")
    earlier = _seed(store, "offset", status="completed",
                    created_at="2026-08-02T00:00:00Z",
                    scheduled_at="2026-08-18T11:00:00+02:00")   # = 09:00Z, before 10:00Z
    ids = _ids(_client(store).get("/bots", headers=HEADERS))
    assert ids == [later, earlier]


def test_malformed_scheduled_at_falls_back_not_500():
    """An unparsable scheduled_at degrades to start_time/created_at — mirror of the SQL
    exception guard in meeting_event_time(); the list never errors on one bad row."""
    store = InMemoryTranscriptStore()
    bad = _seed(store, "bad", status="completed",
                created_at="2026-08-18T08:00:00Z", scheduled_at="not-a-timestamp",
                start_time="2026-08-18T08:00:30Z")
    good = _seed(store, "good", status="completed",
                 created_at="2026-08-17T00:00:00Z", scheduled_at="2026-08-18T09:00:00Z")
    r = _client(store).get("/bots", headers=HEADERS)
    assert r.status_code == 200
    assert _ids(r) == [good, bad]


# ── C3 · internal enumeration is UNCHANGED (created_at DESC) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_internal_enumeration_keeps_created_at_order():
    """Non-list_view callers (get-by-id filter, /bots/status, calendar sync, and the native-id
    resolver's documented 'newest owned row' rule) still see created_at DESC."""
    store = InMemoryTranscriptStore()
    old_scheduled = _seed(store, "old-sched", status="scheduled",
                          created_at="2026-08-10T00:00:00Z", scheduled_at="2026-08-19T09:00:00Z")
    new_terminal = _seed(store, "new-done", status="completed",
                         created_at="2026-08-18T00:00:00Z")
    rows = await store.list_meetings(USER)
    assert [m["id"] for m in rows] == [new_terminal, old_scheduled]
