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
    assert body["deleted"] == {"meetings": 2, "transcripts": 1, "sessions": 0, "objects": 4,
                               "cache_keys": 3, "webhook_deliveries": 0, "reader_entries": 0}
    assert body["retained"] == {"room_meetings": 0}
    assert body["bot_accounts"] == {"deleted": 0, "configured": False}
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
    assert again.json()["deleted"] == {"meetings": 0, "transcripts": 0, "sessions": 0, "objects": 0,
                                       "cache_keys": 0, "webhook_deliveries": 0, "reader_entries": 0}


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


# ── review round: rooms, queued deliveries, reader entries, bot accounts, spawn refusal ──────────

async def test_a_room_s_row_keyed_to_the_payer_is_retained_with_its_objects():
    client, store, repo, storage, redis = await _world()
    store.seed_meeting(meeting_id=12, user_id=GONE, platform="google_meet", native_meeting_id="room-call",
                       status="completed",
                       data={"room_principal": {"principal": "900", "principal_workspace": "w1"},
                             "recordings": [_recording(GONE, 12, 9003)]}, segments=[
                           {"segment_id": "s1", "start": 0, "end": 1, "text": "the room's", "language": "en"}])
    room_objects = ["recordings/7/9003/sess-12/audio/master.wav", "signal/7/12/sess-12/events.jsonl"]
    for key in room_objects:
        storage.blobs[key] = b"room"
    r = await client.delete(f"/internal/users/{GONE}/meetings", headers=_hdr())
    assert r.status_code == 200, r.text
    assert r.json()["retained"] == {"room_meetings": 1}
    assert r.json()["deleted"]["meetings"] == 2
    assert store._meetings[12]["segments"]["s1"]["text"] == "the room's"
    assert all(storage.blobs.get(k) == b"room" for k in room_objects), "the sweep skips retained rows"
    assert "recordings/7/4242/sess-lost/audio/master.wav" not in storage.blobs, "orphans still go"


async def test_queued_webhook_deliveries_of_the_person_s_meetings_are_purged():
    import json as _json

    from meeting_api.webhooks.retry import DEAD_LETTER_KEY, PROCESSING_KEY, RETRY_QUEUE_KEY

    client, store, repo, storage, redis = await _world()
    for key, label in ((RETRY_QUEUE_KEY, "meeting:11"), (PROCESSING_KEY, "meeting:10"),
                       (DEAD_LETTER_KEY, "meeting:11"), (RETRY_QUEUE_KEY, "meeting:20")):
        await redis.rpush(key, _json.dumps({"label": label, "url": "https://hook.test", "payload": {}}))
    r = await client.delete(f"/internal/users/{GONE}/meetings", headers=_hdr())
    assert r.json()["deleted"]["webhook_deliveries"] == 3
    left = [_json.loads(x)["label"] for k in (RETRY_QUEUE_KEY, PROCESSING_KEY, DEAD_LETTER_KEY)
            for x in await redis.lrange(k, 0, -1)]
    assert left == ["meeting:20"], "the other person's delivery stays"


async def test_the_person_leaves_other_people_s_meetings_they_could_read():
    client, store, repo, storage, redis = await _world()
    store._meetings[20]["data"].update({
        "transcript_viewers": [GONE, 99],
        "share_viewers": [{"user_id": GONE, "email": "gone@x.test"}, {"user_id": 99, "email": "k@x.test"}],
        "share_grants": [{"id": "g1", "mode": "restricted", "allowed_emails": ["gone@x.test"]}],
        "share_removed": [{"user_id": GONE, "at": "2026-11-01T00:00:00Z"}]})
    r = await client.delete(f"/internal/users/{GONE}/meetings", headers=_hdr())
    assert r.json()["deleted"]["reader_entries"] == 1
    data = store._meetings[20]["data"]
    assert data["transcript_viewers"] == [99]
    assert data["share_viewers"] == [{"user_id": 99, "email": "k@x.test"}]
    assert "share_removed" not in data
    assert data["share_grants"][0]["revoked"] is True
    assert data["recordings"][0]["id"] == 9002, "the owner's own data is untouched"


async def test_the_person_s_bot_accounts_go_and_a_company_one_stays():
    from meeting_api.bot_accounts import BotAccounts
    from meeting_api.bot_accounts.broker import FakeBroker

    broker = FakeBroker()
    broker.seed({"id": "a" * 32, "owner": f"user:{GONE}", "platform": "google_meet", "display_name": "Mine"})
    broker.seed({"id": "b" * 32, "owner": "company", "platform": "google_meet", "display_name": "Co"})
    broker.seed({"id": "c" * 32, "owner": f"user:{KEEP}", "platform": "google_meet", "display_name": "Theirs"})
    accounts = BotAccounts(broker=broker, runtime=None)
    assert await accounts.erase_owned(GONE) == {"deleted": 1}
    assert sorted(broker.accounts) == ["b" * 32, "c" * 32]
    broker.fail = True
    assert await accounts.erase_owned(KEEP) == {"deleted": 0, "unreachable": True}


async def test_no_bot_is_spawned_for_an_account_being_erased(monkeypatch):
    from meeting_api.bot_spawn import service
    from meeting_api.bot_spawn.ports import AccountErasing

    async def erasing(user_id):
        return {"erasing": True}

    monkeypatch.setattr(service, "_fetch_bot_context", erasing)
    repo = InMemoryMeetingRepo()
    app = create_app(meeting_repo=repo)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ma")
    r = await client.post("/bots", headers={"x-user-id": str(GONE)},
                          json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "account_erasing"
    assert repo._meetings == {}, "refused before any row is written"
    assert issubclass(AccountErasing, service.SpawnFailed), "the calendar sweep refuses it too"


async def test_identity_answering_no_such_account_reads_as_erasing(monkeypatch):
    from meeting_api.bot_spawn import service

    class _R:
        status_code = 404

        def json(self):
            return {"detail": "User not found"}

    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _R()

    monkeypatch.setenv("ADMIN_API_URL", "http://admin-api:8001")
    monkeypatch.setenv("INTERNAL_API_SECRET", "s")
    monkeypatch.setattr(httpx, "AsyncClient", _C)
    assert await service._fetch_bot_context(GONE) == {"erasing": True}

