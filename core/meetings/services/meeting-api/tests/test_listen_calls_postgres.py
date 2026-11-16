"""`listen_calls` over the REAL store: the listening tick's one narrow read (design mvp1-shared-listen
§2-§3) — the running calls on each access branch plus the held ones, each with its access label
(owner | viewer | workspace | None) and its transcript feed mark, read from Postgres and Redis.

The in-memory suite (`test_link_events.py`) drives the fake; this drives the SQL, so a fake that
agrees with itself but not with Postgres cannot hide here. Opt-in: MEETING_API_TEST_DATABASE_URL (an
EMPTY database, postgresql+asyncpg://…); MEETING_API_TEST_REDIS_URL adds the feed marks."""
from __future__ import annotations

import os

import pytest

URL = os.getenv("MEETING_API_TEST_DATABASE_URL")
REDIS_URL = os.getenv("MEETING_API_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set MEETING_API_TEST_DATABASE_URL to an empty Postgres database")

ME, OWNER = 7, 8
RUNNING = ("requested", "joining", "awaiting_admission", "active", "needs_help", "needs_human_help", "stopping")

_EVENT_TIME = """
CREATE OR REPLACE FUNCTION meeting_event_time(data jsonb, start_time timestamp, created_at timestamp)
RETURNS timestamp LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
BEGIN
    RETURN COALESCE(((data ->> 'scheduled_at')::timestamptz AT TIME ZONE 'UTC'), start_time, created_at);
EXCEPTION WHEN OTHERS THEN
    RETURN COALESCE(start_time, created_at);
END $fn$;
"""


async def _store():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from meeting_api.collector.adapters import SqlAlchemyTranscriptStore
    from meeting_api.sessions.models import Base

    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(text(_EVENT_TIME))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    redis = None
    if REDIS_URL:
        import redis.asyncio as aioredis
        redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sf, redis, SqlAlchemyTranscriptStore(sf, redis_client=redis)


async def _seed(sf, rows):
    from meeting_api.sessions.models import Meeting

    ids = []
    async with sf() as db:
        for user_id, status, native, data in rows:
            m = Meeting(user_id=user_id, platform="google_meet", platform_specific_id=native,
                        status=status, data=data)
            db.add(m)
            await db.flush()
            ids.append(m.id)
        await db.commit()
    return ids


async def test_the_narrow_read_labels_each_branch_and_reads_held_calls_whatever_their_state():
    engine, sf, redis, store = await _store()
    try:
        mine, shared, team, stranger, room, done, deleted = await _seed(sf, [
            (ME, "active", "mine-aaa", {"title": "Mine"}),
            (OWNER, "active", "shared-bbb", {"title": "Theirs", "transcript_viewers": [ME]}),
            (OWNER, "needs_help", "team-ccc", {"workspace_id": "ws_team"}),
            (OWNER, "active", "stranger-ddd", {"title": "Not yours"}),
            (ME, "active", "room-eee", {"room_principal": {"principal": 99}, "workspace_id": "ws_room"}),
            (ME, "completed", "done-fff", {}),
            (OWNER, "active", "deleted-ggg", {"transcript_viewers": [ME],
                                               "artifact_deletion": {"state": "completed"}}),
        ])
        if redis is not None:
            await redis.delete(f"tc:meeting:{mine}", f"tc:meeting:{shared}")
            await redis.xadd(f"tc:meeting:{shared}", {"text": "hello"})
        rows = {r["id"]: r for r in await store.listen_calls(
            ME, member_workspaces={"ws_team"}, held_ids=[], statuses=RUNNING)}
        assert {i: r["access"] for i, r in rows.items()} == {
            mine: "owner", shared: "viewer", team: "workspace"}
        assert rows[shared]["title"] == "Theirs" and rows[shared]["user_id"] == OWNER
        assert not {stranger, room, done, deleted} & set(rows), "no access, a room's, ended, deleted"
        if redis is not None:
            assert rows[mine]["mark"] == "" and rows[shared]["mark"], rows
            before = rows[shared]["mark"]
            await redis.xadd(f"tc:meeting:{shared}", {"text": "settled"})
            again = {r["id"]: r for r in await store.listen_calls(ME, held_ids=[], statuses=RUNNING)}
            assert again[shared]["mark"] != before
        # held ids come back whatever their status or access: ended, a stranger's, a room's
        held = {r["id"]: r for r in await store.listen_calls(
            ME, held_ids=[done, stranger, room, deleted, 987654], statuses=())}
        assert {i: (r.get("status"), r["access"]) for i, r in held.items()} == {
            done: ("completed", "owner"), stranger: (None, None), room: (None, None),
            deleted: (None, None)}
        assert held[done]["mark"] is None, "no feed mark outside a running call"
        for i in (stranger, room, deleted):
            assert held[i] == {"id": i, "access": None}, "a held call they cannot read: its id alone (H1)"
        # a room's call is read through its workspace, never as owned
        via_ws = {r["id"]: r for r in await store.listen_calls(
            ME, member_workspaces={"ws_room"}, statuses=RUNNING)}
        assert via_ws[room]["access"] == "workspace"
    finally:
        await engine.dispose()
        if redis is not None:
            await redis.aclose()
