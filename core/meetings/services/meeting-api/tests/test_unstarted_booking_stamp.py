"""ADR-0052 §3 (revised 4 Nov 2026, no fallback clock): a booking whose bot no start call reached by
its deadline (start + ``BOOKED_GRACE_S``) says so on its row — ``data.auto_join_error`` = "not
started: scheduler unavailable" — stamped by meeting-api's reconciler. "Never started" is read off
the row's own fields (no ``auto_join_last_attempt``, no ``auto_join_error``). Idempotent, and no call
to Flows. The SQL repo's half (one conditional UPDATE, no outbox fact) is in
``test_planned_outbox_pg.py``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from meeting_api import events as events_mod
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo

NOW = datetime(2030, 3, 1, 9, 30, tzinfo=timezone.utc)
STAMP = "not started: scheduler unavailable"
log = logging.getLogger("test.unstarted")


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _booking(repo, *, start, **data):
    row = asyncio.run(repo.create_meeting(user_id=7, platform="google_meet",
                                          native_meeting_id=f"n-{len(repo._meetings)}",
                                          data={"auto_join": True, "scheduled_at": _iso(start),
                                                **data}))
    repo._meetings[row["id"]]["status"] = "scheduled"
    return row["id"]


@pytest.fixture(autouse=True)
def no_flows_call(monkeypatch):
    """Any HTTP client or Flows publish opened by the stamp fails the test."""
    def _no_client(*a, **kw):
        raise AssertionError("the past-deadline stamp opened an HTTP client")

    async def _no_publish(*a, **kw):
        raise AssertionError("the past-deadline stamp published to Flows")

    monkeypatch.setattr(httpx, "AsyncClient", _no_client)
    monkeypatch.setattr(events_mod, "publish", _no_publish)
    monkeypatch.setenv("BOOKED_GRACE_S", "600")


def _stamp(repo, now=NOW) -> int:
    from meeting_api.lifecycle.reconcile import stamp_unstarted_bookings

    return asyncio.run(stamp_unstarted_bookings(repo, log=log, now=now))


def test_a_booking_past_its_deadline_with_no_start_call_is_stamped_once():
    repo = InMemoryMeetingRepo()
    mid = _booking(repo, start=NOW - timedelta(seconds=601))
    assert _stamp(repo) == 1
    row = repo._meetings[mid]
    assert row["data"]["auto_join_error"] == STAMP
    assert row["status"] == "scheduled", "the stamp starts nothing and moves nothing"
    assert _stamp(repo) == 0, "a second pass stamped again"
    assert repo._meetings[mid]["data"]["auto_join_error"] == STAMP


def test_inside_its_window_a_booking_is_not_stamped():
    repo = InMemoryMeetingRepo()
    at_deadline = _booking(repo, start=NOW - timedelta(seconds=600))
    ahead = _booking(repo, start=NOW + timedelta(minutes=30))
    assert _stamp(repo) == 0
    for mid in (at_deadline, ahead):
        assert "auto_join_error" not in repo._meetings[mid]["data"]


def test_a_booking_a_start_call_reached_is_not_stamped():
    repo = InMemoryMeetingRepo()
    past = NOW - timedelta(hours=1)
    attempted = _booking(repo, start=past, auto_join_last_attempt=_iso(past - timedelta(minutes=10)))
    refused = _booking(repo, start=past, auto_join_error="bot concurrency limit reached")
    assert _stamp(repo) == 0
    assert "auto_join_error" not in repo._meetings[attempted]["data"]
    assert repo._meetings[refused]["data"]["auto_join_error"] == "bot concurrency limit reached"


def test_a_plan_without_a_bot_is_not_stamped():
    repo = InMemoryMeetingRepo()
    past = NOW - timedelta(hours=1)
    off = _booking(repo, start=past, auto_join=False)
    stopped = _booking(repo, start=past, stop_requested=True)
    idle = _booking(repo, start=past)
    repo._meetings[idle]["status"] = "idle"
    assert _stamp(repo) == 0
    for mid in (off, stopped, idle):
        assert "auto_join_error" not in repo._meetings[mid]["data"]


def test_the_grace_is_meeting_apis_booked_grace(monkeypatch):
    monkeypatch.setenv("BOOKED_GRACE_S", "60")
    repo = InMemoryMeetingRepo()
    mid = _booking(repo, start=NOW - timedelta(seconds=61))
    assert _stamp(repo) == 1
    assert repo._meetings[mid]["data"]["auto_join_error"] == STAMP


def test_the_reconcile_loop_stamps_it(monkeypatch):
    """Wiring: meeting-api's stop-reconcile loop (``__main__._attach_background_loops``) runs the
    stamp on its pass, with no Flows configured at all."""
    import fakeredis.aioredis

    from meeting_api import create_app
    from meeting_api.__main__ import _attach_background_loops
    from meeting_api.collector.adapters import RedisStreamBus
    from meeting_api.collector.fakes import InMemoryTranscriptStore

    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)

    class _Repo(InMemoryMeetingRepo):
        async def list_stale_stopping(self, *, older_than_seconds):
            return []

    repo = _Repo()
    mid = _booking(repo, start=datetime.now(timezone.utc) - timedelta(hours=1))
    app = create_app(meeting_repo=repo)

    async def _drive():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        _attach_background_loops(app, InMemoryTranscriptStore(), RedisStreamBus(redis), redis,
                                 meeting_repo=repo, runtime=None)
        async with app.router.lifespan_context(app):
            for _ in range(50):
                if repo._meetings[mid]["data"].get("auto_join_error"):
                    break
                await asyncio.sleep(0.05)

    asyncio.run(_drive())
    assert repo._meetings[mid]["data"].get("auto_join_error") == STAMP
