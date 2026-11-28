"""Account erase (P15): ``DELETE /internal/users/{user_id}/meetings`` — every meeting of one person
gone, rows included, with its objects, transcripts and Redis keys; nobody else's touched.

Driven over the shipped app with the in-memory stores and fakeredis (no docker). The SQL store's
half of the same contract is ``erase_account_meeting`` in ``collector/adapters.py``; the admin-api
Postgres test (``test_admin_user_delete.py``) runs the account delete end to end through this door.
"""
from __future__ import annotations

import asyncio

import fakeredis.aioredis
import httpx
import pytest

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.ports import account_cache_keys, account_cache_patterns
from meeting_api.recordings.fakes import InMemoryStorage

SECRET = "internal-secret-for-the-account-erase"
GONE, KEEP = 7, 8


def _recording(uid: int, mid: int, rid: int) -> dict:
    return {"id": rid, "meeting_id": mid, "user_id": uid, "session_uid": f"sess-{mid}",
            "status": "completed",
            "media_files": [{"id": 1, "type": "audio", "format": "wav",
                             "storage_path": f"recordings/{uid}/{rid}/sess-{mid}/audio/master.wav"}]}


async def _world(*, live: bool = False, storage_cls=InMemoryStorage, secret: str | None = SECRET):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = InMemoryTranscriptStore(redis_client=redis)
    repo = InMemoryMeetingRepo()
    storage = storage_cls()
    seg = [{"segment_id": "s1", "start": 0, "end": 1, "text": "confidential", "language": "en"}]
    # the person: a planned meeting, a completed one with a transcript, a recording and a tape
    store.seed_meeting(meeting_id=10, user_id=GONE, platform="google_meet",
                       native_meeting_id="planned-room", status="scheduled")
    store.seed_meeting(meeting_id=11, user_id=GONE, platform="google_meet",
                       native_meeting_id="done-room", status="completed",
                       data={"recordings": [_recording(GONE, 11, 9001)], "notes": "summary"},
                       segments=seg)
    for key in ("recordings/7/9001/sess-11/audio/000000.wav",
                "recordings/7/9001/sess-11/audio/master.wav",
                "signal/7/11/sess-11/events.jsonl",
                "recordings/7/4242/sess-lost/audio/master.wav"):   # an object no row names any more
        storage.blobs[key] = b"x"
    if live:
        m = await repo.create_meeting(user_id=GONE, platform="google_meet",
                                      native_meeting_id="live-room", data={})
        store.seed_meeting(meeting_id=m["id"], user_id=GONE, platform="google_meet",
                           native_meeting_id="live-room", status="active")
        repo._meetings[m["id"]]["status"] = "active"
    # somebody else: must come through untouched
    store.seed_meeting(meeting_id=20, user_id=KEEP, platform="google_meet",
                       native_meeting_id="their-room", status="completed",
                       data={"recordings": [_recording(KEEP, 20, 9002)]}, segments=seg)
    storage.blobs["recordings/8/9002/sess-20/audio/master.wav"] = b"theirs"
    storage.blobs["signal/8/20/sess-20/events.jsonl"] = b"theirs"
    for uid in (GONE, KEEP):
        await redis.lpush(f"webhook:deliveries:{uid}", "{}")
        await redis.set(f"cal:sync:{uid}", "{}")
        await redis.set(f"cal:sync:{uid}:cal-1", "{}")
    for mid in (11, 20):
        await redis.hset(f"meeting:{mid}:segments", "s1", "{}")
        await redis.sadd("active_meetings", str(mid))
    app = create_app(meeting_repo=repo, transcript_store=store, storage=storage,
                     internal_secret=secret, open_callbacks=True)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ma")
    return client, store, repo, storage, redis


def _hdr():
    return {"X-Internal-Secret": SECRET}


async def _keys(redis) -> set:
    return set(await redis.keys("*"))


async def test_erase_leaves_nothing_of_the_person_and_everything_of_everyone_else():
    client, store, repo, storage, redis = await _world()
    r = await client.delete(f"/internal/users/{GONE}/meetings", headers=_hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == {"meetings": 2, "transcripts": 1, "sessions": 0,
                               "objects": 4, "cache_keys": 3}
    # zero rows, objects and keys for the person
    assert [m for m in store._meetings.values() if m["user_id"] == GONE] == []
    assert [k for k in storage.blobs if k.startswith(("recordings/7/", "signal/7/"))] == []
    left = await _keys(redis)
    assert not [k for k in left if ":7" in k or k.startswith("meeting:11:")]
    assert "11" not in await redis.smembers("active_meetings")
    # the other person is exactly where they were
    assert store._meetings[20]["segments"]["s1"]["text"] == "confidential"
    assert store._meetings[20]["data"]["recordings"][0]["id"] == 9002
    assert storage.blobs["recordings/8/9002/sess-20/audio/master.wav"] == b"theirs"
    assert storage.blobs["signal/8/20/sess-20/events.jsonl"] == b"theirs"
    assert {"webhook:deliveries:8", "cal:sync:8", "cal:sync:8:cal-1", "meeting:20:segments"} <= left
    assert "20" in await redis.smembers("active_meetings")


async def test_a_second_erase_is_a_200_with_nothing_left_to_delete():
    client, store, repo, storage, redis = await _world()
    assert (await client.delete(f"/internal/users/{GONE}/meetings", headers=_hdr())).status_code == 200
    again = await client.delete(f"/internal/users/{GONE}/meetings", headers=_hdr())
    assert again.status_code == 200
    assert again.json()["deleted"] == {"meetings": 0, "transcripts": 0, "sessions": 0,
                                       "objects": 0, "cache_keys": 0}


async def test_only_the_internal_tier_may_erase():
    client, store, repo, storage, redis = await _world()
    assert (await client.delete(f"/internal/users/{GONE}/meetings")).status_code == 403
    assert (await client.delete(f"/internal/users/{GONE}/meetings",
                                headers={"X-Internal-Secret": "wrong"})).status_code == 403
    unset, *_ = await _world(secret=None)
    assert (await unset.delete(f"/internal/users/{GONE}/meetings", headers=_hdr())).status_code == 503
    assert len([m for m in store._meetings.values() if m["user_id"] == GONE]) == 2


async def test_a_live_bot_is_asked_to_leave_and_nothing_is_deleted_until_it_has():
    client, store, repo, storage, redis = await _world(live=True)
    live_id = next(mid for mid, m in store._meetings.items() if m["native_meeting_id"] == "live-room")
    r = await client.delete(f"/internal/users/{GONE}/meetings?wait_s=0.2", headers=_hdr())
    assert r.status_code == 409, r.text
    body = r.json()
    assert [row["id"] for row in body["running"]] == [live_id]
    assert body["stop_requested"] == [live_id]
    assert repo._meetings[live_id]["data"].get("stop_requested") is True, "the bot was asked to leave"
    assert len([m for m in store._meetings.values() if m["user_id"] == GONE]) == 3, "nothing deleted"
    assert "recordings/7/9001/sess-11/audio/master.wav" in storage.blobs
    # the bot leaves (one database in production; two fakes here), and the retry finishes the job
    store._meetings[live_id]["status"] = "completed"
    done = await client.delete(f"/internal/users/{GONE}/meetings", headers=_hdr())
    assert done.status_code == 200, done.text
    assert done.json()["deleted"]["meetings"] == 3
    assert [m for m in store._meetings.values() if m["user_id"] == GONE] == []


async def test_a_bot_that_leaves_within_the_wait_is_erased_in_the_same_call():
    client, store, repo, storage, redis = await _world(live=True)
    live_id = next(mid for mid, m in store._meetings.items() if m["native_meeting_id"] == "live-room")

    async def _leaves():
        await asyncio.sleep(0.3)
        store._meetings[live_id]["status"] = "completed"

    task = asyncio.create_task(_leaves())
    r = await client.delete(f"/internal/users/{GONE}/meetings?wait_s=5", headers=_hdr())
    await task
    assert r.status_code == 200, r.text
    assert r.json()["stop_requested"] == [live_id]
    assert r.json()["deleted"]["meetings"] == 3


async def test_a_storage_failure_deletes_no_row_and_a_retry_finishes():
    class FailsOnce(InMemoryStorage):
        fail = True

        async def delete(self, key: str) -> None:
            if FailsOnce.fail:
                FailsOnce.fail = False
                raise RuntimeError("injected object-store failure")
            await super().delete(key)

    client, store, repo, storage, redis = await _world(storage_cls=FailsOnce)
    with pytest.raises(RuntimeError):
        await client.delete(f"/internal/users/{GONE}/meetings", headers=_hdr())
    assert 11 in store._meetings and store._meetings[11]["segments"], "objects first: the row survives"
    r = await client.delete(f"/internal/users/{GONE}/meetings", headers=_hdr())
    assert r.status_code == 200, r.text
    assert [m for m in store._meetings.values() if m["user_id"] == GONE] == []
    assert [k for k in storage.blobs if k.startswith(("recordings/7/", "signal/7/"))] == []


def test_the_person_s_cache_keys_are_the_ones_their_writers_write():
    """Tie the erase's key list to the writers, so a renamed key cannot silently survive an erase."""
    from meeting_api.bot_spawn import fakes as _unused  # noqa: F401 — package imports cleanly
    from meeting_api.calendar_sync import runner
    from meeting_api.webhooks.ledger import RedisDeliveryLedger

    class _Recorder:
        def __init__(self):
            self.keys = []

        async def set(self, key, *_a, **_k):
            self.keys.append(key)

        async def lpush(self, key, *_a):
            self.keys.append(key)

        async def ltrim(self, *_a):
            pass

    rec = _Recorder()
    asyncio.run(RedisDeliveryLedger(rec).record(GONE, {"event": "x"}))
    asyncio.run(runner.store_stamp(rec, GONE, {"last_sync": "t"}))
    asyncio.run(runner.store_stamp(rec, GONE, {"last_sync": "t"}, calendar_id="cal-1"))
    import fnmatch

    keys, patterns = set(account_cache_keys(GONE)), account_cache_patterns(GONE)
    for key in rec.keys:
        assert key in keys or any(fnmatch.fnmatch(key, p) for p in patterns), key
