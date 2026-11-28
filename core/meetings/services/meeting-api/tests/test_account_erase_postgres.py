"""Account erase over the REAL SQL store: ``DELETE /internal/users/{user_id}/meetings`` hard-deletes
the person's meeting rows, sessions and transcripts in Postgres, refuses a row a bot still owns, and
leaves another person's rows alone. The in-memory suite (``test_account_erase.py``) covers objects,
Redis and the stop path; this proves the SQL ``erase_account_meeting`` and ``account_meetings`` run.

Opt-in: set MEETING_API_TEST_DATABASE_URL to an EMPTY database (postgresql+asyncpg://…). The test
creates the tables it needs and drops them afterwards.
"""
from __future__ import annotations

import os

import httpx
import pytest

URL = os.getenv("MEETING_API_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set MEETING_API_TEST_DATABASE_URL to an empty Postgres database")

SECRET = "internal-secret-for-the-account-erase"
GONE, KEEP = 601, 602

# The index function the schema's event-order indexes need (the migration owns it in production).
_EVENT_TIME = """
CREATE OR REPLACE FUNCTION meeting_event_time(data jsonb, start_time timestamp, created_at timestamp)
RETURNS timestamp LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
BEGIN
    RETURN COALESCE(((data ->> 'scheduled_at')::timestamptz AT TIME ZONE 'UTC'), start_time, created_at);
EXCEPTION WHEN OTHERS THEN
    RETURN COALESCE(start_time, created_at);
END $fn$;
"""


async def test_erase_hard_deletes_rows_sessions_and_transcripts_in_postgres():
    from sqlalchemy import func, select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from meeting_api.app import create_app
    from meeting_api.collector.adapters import SqlAlchemyTranscriptStore
    from meeting_api.recordings.fakes import InMemoryStorage
    from meeting_api.sessions.models import Base, Meeting, MeetingSession, Transcription

    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(text(_EVENT_TIME))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            for mid, uid, status, data in (
                    (1, GONE, "scheduled", {}), (2, GONE, "completed", {}), (3, GONE, "active", {}),
                    (4, KEEP, "completed", {"transcript_viewers": [GONE],
                                            "share_viewers": [{"user_id": GONE, "email": "g@x.test"}]}),
                    (5, GONE, "completed", {"room_principal": {"principal": "900"}})):
                db.add(Meeting(id=mid, user_id=uid, platform="google_meet",
                               platform_specific_id=f"room-{mid}", status=status, data=data))
            await db.flush()
            for mid in (2, 3, 4):
                db.add(MeetingSession(meeting_id=mid, session_uid=f"sess-{mid}"))
                db.add(Transcription(meeting_id=mid, start_time=0, end_time=1, text="confidential"))
            await db.commit()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(
            transcript_store=SqlAlchemyTranscriptStore(sf), storage=InMemoryStorage(),
            internal_secret=SECRET)), base_url="http://meeting-api")

        async def count(model, *where):
            async with sf() as db:
                return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar()

        # meeting 3 has a bot that never leaves: refused, nothing deleted
        r = await client.delete(f"/internal/users/{GONE}/meetings?wait_s=0",
                                headers={"X-Internal-Secret": SECRET})
        assert r.status_code == 409, r.text
        assert [row["id"] for row in r.json()["running"]] == [3]
        assert await count(Meeting, Meeting.user_id == GONE) == 4

        async with sf() as db:
            (await db.get(Meeting, 3)).status = "completed"
            await db.commit()
        r = await client.delete(f"/internal/users/{GONE}/meetings", headers={"X-Internal-Secret": SECRET})
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] == {"meetings": 3, "transcripts": 2, "sessions": 2, "objects": 0,
                                       "cache_keys": 0, "webhook_deliveries": 0, "reader_entries": 1}
        assert r.json()["retained"] == {"room_meetings": 1}
        assert await count(Meeting, Meeting.user_id == GONE) == 1, "the room's row stays"
        async with sf() as db:
            other = (await db.get(Meeting, 4)).data
        assert other.get("transcript_viewers") == [] and other.get("share_viewers") == []
        assert await count(MeetingSession, MeetingSession.meeting_id.in_([1, 2, 3])) == 0
        assert await count(Transcription, Transcription.meeting_id.in_([1, 2, 3])) == 0
        # the other person's row, session and transcript are untouched
        assert await count(Meeting, Meeting.user_id == KEEP) == 1
        assert await count(MeetingSession, MeetingSession.meeting_id == 4) == 1
        assert await count(Transcription, Transcription.meeting_id == 4) == 1

        again = await client.delete(f"/internal/users/{GONE}/meetings", headers={"X-Internal-Secret": SECRET})
        assert again.status_code == 200 and again.json()["deleted"]["meetings"] == 0
        await client.aclose()
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
