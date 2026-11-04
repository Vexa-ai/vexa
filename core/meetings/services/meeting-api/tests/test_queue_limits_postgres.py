"""The capacity queue and the seat cooldown against real Postgres: the SQL adapters' statements.

Opt-in: set MEETING_API_TEST_DATABASE_URL to an EMPTY Postgres database (the schema is created and
dropped here), e.g. a throwaway `postgres:16` container."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from test_meeting_bundle_postgres import _EVENT_TIME

URL = os.getenv("MEETING_API_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set MEETING_API_TEST_DATABASE_URL to an empty Postgres database")
USER = 7


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


async def _with_db(body):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from meeting_api.bot_spawn.adapters import SqlAlchemyMeetingRepo
    from meeting_api.collector.adapters import SqlAlchemyTranscriptStore
    from meeting_api.sessions.models import Base

    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(text(_EVENT_TIME))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await body(sf, SqlAlchemyMeetingRepo(sf), SqlAlchemyTranscriptStore(sf))
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _meeting(sf, *, status, native, data=None, end_time=None, session=None, container=None,
                   started=None):
    from meeting_api.sessions.models import Meeting, MeetingSession

    async with sf() as db:
        m = Meeting(user_id=USER, platform="google_meet", platform_specific_id=native, status=status,
                    data=dict(data or {}), end_time=end_time, bot_container_id=container)
        db.add(m)
        await db.flush()
        if session:
            kw = {"session_start_time": started} if started else {}
            db.add(MeetingSession(meeting_id=m.id, session_uid=session, **kw))
        await db.commit()
        return m.id


def test_queue_writes_are_conditional_and_listing_reads_paths_only():
    async def body(sf, repo, store):
        mid = await _meeting(sf, status="requested", native="q-1", session="s-1", container="w-1",
                             data={"scheduled_at": "2026-11-04T10:00:00Z"})
        await _meeting(sf, status="requested", native="q-2", session="s-2", container=None)
        (cand,) = await repo.list_queue_candidates()
        assert cand == {"meeting_id": mid, "status": "requested", "session_uid": "s-1",
                        "bot_container_id": "w-1", "queue": None,
                        "scheduled_at": "2026-11-04T10:00:00Z", "stop_requested": False}
        entry = {"from": "requested", "to": "queued", "source": "capacity_queue"}
        row = await repo.set_queue_status(session_uid="s-1", expected=("requested",),
                                          status_reason="cluster_at_capacity",
                                          queue_patch={"queued_at": "t0", "status_reason": "cluster_at_capacity"},
                                          trail_entry=entry)
        assert row["status"] == "queued" and row["data"]["queue"]["queued_at"] == "t0"
        assert await repo.set_queue_status(session_uid="s-1", expected=("requested",),
                                           status_reason="x", queue_patch={}, trail_entry=entry) is None
        late = await repo.set_queue_status(session_uid="s-1", expected=("queued",),
                                           status_reason="will_join_late",
                                           queue_patch={"status_reason": "will_join_late"}, trail_entry=entry)
        assert late["data"]["queue"] == {"queued_at": "t0", "status_reason": "will_join_late"}
        assert len(late["data"]["status_transition"]) == 2
        # the API: /bots/status with queued in its set, and the hoisted status_reason
        running = await store.list_meetings(
            USER, status=("requested", "queued", "joining", "awaiting_admission", "active", "stopping"),
            slim=True)
        assert {m["status"] for m in running} == {"queued", "requested"}
        queued = next(m for m in running if m["status"] == "queued")
        assert queued["status_reason"] == "will_join_late"
        assert await repo.count_active_bots(user_id=USER) == 2

    asyncio.run(_with_db(body))


def test_the_seat_cooldown_and_the_churn_counts():
    async def body(sf, repo, store):
        now = datetime.now(timezone.utc)
        await _meeting(sf, status="completed", native="c-1", end_time=_naive(now - timedelta(seconds=10)),
                       data={"stop_requested": True}, session="s-c1", started=now - timedelta(seconds=25))
        await _meeting(sf, status="completed", native="c-2", end_time=_naive(now - timedelta(seconds=90)),
                       data={"stop_requested": True}, session="s-c2", started=now - timedelta(minutes=30))
        await _meeting(sf, status="completed", native="c-3", end_time=_naive(now - timedelta(seconds=10)),
                       data={"stop_requested": True, "scheduled_at": "2026-11-04T10:00:00Z"})
        await _meeting(sf, status="failed", native="c-4", end_time=_naive(now - timedelta(seconds=10)))
        assert await repo.count_active_bots(user_id=USER) == 0
        assert await repo.count_active_bots(user_id=USER, cooldown_s=60) == 1
        from meeting_api.bot_spawn.ports import MaxBotsExceeded

        with pytest.raises(MaxBotsExceeded):
            await repo.create_meeting_guarded(user_id=USER, platform="google_meet",
                                              native_meeting_id="n-1", data={}, max_concurrent=1,
                                              cooldown_s=60)
        row = await repo.create_meeting_guarded(user_id=USER, platform="google_meet",
                                                native_meeting_id="n-1", data={}, max_concurrent=1)
        assert row["status"] == "requested"
        (counts,) = await repo.bot_run_stop_counts(window_s=86_400, quick_stop_s=30)
        assert counts == {"user_id": USER, "runs": 2, "quick_stops": 1}

    asyncio.run(_with_db(body))
