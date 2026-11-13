"""room_history over the REAL SQL (bot_spawn/adapters.py, D402): one room's rows of every person, the
window, newest first, and only the fields the verdict reads. Opt-in: MEETING_API_TEST_DATABASE_URL."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

URL = os.getenv("MEETING_API_TEST_DATABASE_URL")
# The function the meetings table's event-time index needs (as test_meeting_bundle_postgres.py creates it).
_EVENT_TIME = """
CREATE OR REPLACE FUNCTION meeting_event_time(data jsonb, start_time timestamp, created_at timestamp)
RETURNS timestamp LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
BEGIN
    RETURN COALESCE(((data ->> 'scheduled_at')::timestamptz AT TIME ZONE 'UTC'), start_time, created_at);
EXCEPTION WHEN OTHERS THEN
    RETURN COALESCE(start_time, created_at);
END $fn$;
"""
pytestmark = pytest.mark.skipif(not URL, reason="set MEETING_API_TEST_DATABASE_URL to an empty Postgres database")


def test_room_history_reads_one_rooms_window_with_the_verdicts_fields_only():
    async def go():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from meeting_api.bot_spawn import room_memory as rm
        from meeting_api.bot_spawn.adapters import SqlAlchemyMeetingRepo
        from meeting_api.sessions.models import Base, Meeting

        from sqlalchemy import text

        engine = create_async_engine(URL)
        async with engine.begin() as c:
            await c.execute(text(_EVENT_TIME))
            await c.run_sync(Base.metadata.drop_all)
            await c.run_sync(Base.metadata.create_all)
        sf = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        refused_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        async with sf() as db:
            db.add_all([
                Meeting(user_id=1, platform="google_meet", platform_specific_id="aaa-bbbb-ccc", status="failed",
                        created_at=now - timedelta(days=2),
                        data={"meet_respawn": {"attempts": [{"at": refused_at, "signed_in": False}]},
                              "secret_stuff": "x"}),
                Meeting(user_id=2, platform="google_meet", platform_specific_id="aaa-bbbb-ccc", status="completed",
                        created_at=now - timedelta(days=1), start_time=now - timedelta(days=1),
                        data={"auth_userdata_path": "bot-account:abc"}),
                Meeting(user_id=3, platform="google_meet", platform_specific_id="aaa-bbbb-ccc", status="completed",
                        created_at=now - timedelta(days=40), start_time=now - timedelta(days=40), data={}),
                Meeting(user_id=1, platform="google_meet", platform_specific_id="zzz-zzzz-zzz", status="completed",
                        created_at=now - timedelta(days=1), start_time=now - timedelta(days=1), data={}),
            ])
            await db.commit()
        repo = SqlAlchemyMeetingRepo(sf)
        rows = await repo.room_history("google_meet", "aaa-bbbb-ccc", rm.since(datetime.now(timezone.utc)))
        assert [r["status"] for r in rows] == ["completed", "failed"]          # newest first, 40-day row out
        assert all("secret_stuff" not in r["data"] for r in rows)              # only the verdict's fields
        assert rows[0]["data"]["auth_userdata_path"] == "bot-account:abc"
        assert rows[1]["data"]["meet_respawn"]["attempts"][0]["signed_in"] is False
        assert rm.verdict(rows) == rm.NEEDS_SIGN_IN                             # the signed-in row decides nothing
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.drop_all)
        await engine.dispose()
    asyncio.run(go())
