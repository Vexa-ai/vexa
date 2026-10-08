"""The one-time operator sweep erases the Redis transcript keys of every deleted meeting, and only those.

Meetings deleted before the delete route erased their Redis keys itself still hold
`tc:meeting:{id}`. `python -m meeting_api.collector.erased_feed_sweep` removes the same keys the
delete route removes, for every row carrying the deletion stamp.

Offline: the in-memory store and fakeredis; no Postgres.
"""
from __future__ import annotations

import inspect
import json

import fakeredis
import fakeredis.aioredis
import pytest

from meeting_api.collector import erased_feed_sweep
from meeting_api.collector.db_writer import ACTIVE_MEETINGS_KEY
from meeting_api.collector.erased_feed_sweep import sweep_erased_feeds
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.ports import erased_meeting_cache_keys

DELETED_WITH_FEED, DELETED_CLEAN, PENDING, LIVE = 51, 52, 53, 54
STAMP = {"state": "completed", "scope": "primary_transcript_recording_and_fixture_storage"}


def _seed(sync, meeting_id: int) -> None:
    sync.hset(f"meeting:{meeting_id}:segments", "s1", json.dumps({"text": "kept at rest"}))
    sync.xadd(f"tc:meeting:{meeting_id}", {"payload": json.dumps(
        {"type": "transcription", "segments": [{"segment_id": "s1", "text": "kept at rest"}]})})
    sync.sadd(ACTIVE_MEETINGS_KEY, str(meeting_id))


@pytest.fixture
def world():
    server = fakeredis.FakeServer()
    sync = fakeredis.FakeRedis(server=server, decode_responses=True)
    store = InMemoryTranscriptStore()
    for mid, data in ((DELETED_WITH_FEED, {"artifact_deletion": STAMP}),
                      (DELETED_CLEAN, {"artifact_deletion": STAMP}),
                      (PENDING, {"artifact_deletion": {**STAMP, "state": "pending"}}),
                      (LIVE, {})):
        store.seed_meeting(meeting_id=mid, user_id=7, platform="google_meet",
                           native_meeting_id=f"room-{mid}", status="completed", data=data)
    for mid in (DELETED_WITH_FEED, PENDING, LIVE):
        _seed(sync, mid)
    return store, server, sync


def _present(sync, meeting_id: int) -> list[str]:
    return [k for k in erased_meeting_cache_keys(meeting_id) if sync.exists(k)]


async def test_the_sweep_erases_every_deleted_meetings_keys_and_no_others(world):
    store, server, sync = world
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    result = await sweep_erased_feeds(store, client)

    assert _present(sync, DELETED_WITH_FEED) == [] and _present(sync, PENDING) == []
    assert not sync.sismember(ACTIVE_MEETINGS_KEY, str(DELETED_WITH_FEED))
    assert _present(sync, LIVE) == [f"meeting:{LIVE}:segments", f"tc:meeting:{LIVE}"]
    assert sync.sismember(ACTIVE_MEETINGS_KEY, str(LIVE)), "a meeting that was not deleted was touched"
    assert result == {"erased_meetings": 3, "meetings_with_keys": 2, "keys_deleted": 4, "dry_run": False}
    await client.aclose()


async def test_a_dry_run_counts_and_deletes_nothing(world):
    store, server, sync = world
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    result = await sweep_erased_feeds(store, client, dry_run=True)

    assert result == {"erased_meetings": 3, "meetings_with_keys": 2, "keys_found": 4, "dry_run": True}
    assert _present(sync, DELETED_WITH_FEED) == [f"meeting:{DELETED_WITH_FEED}:segments",
                                                  f"tc:meeting:{DELETED_WITH_FEED}"]
    await client.aclose()


async def test_the_sweep_is_idempotent(world):
    store, server, _sync = world
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    await sweep_erased_feeds(store, client)
    again = await sweep_erased_feeds(store, client)
    assert again["meetings_with_keys"] == 0 and again["keys_deleted"] == 0
    await client.aclose()


def test_the_command_line_prints_one_json_summary(monkeypatch, capsys):
    seen = {}

    async def fake_run(dry_run):
        seen["dry_run"] = dry_run
        return {"erased_meetings": 0, "meetings_with_keys": 0, "keys_found": 0, "dry_run": dry_run}

    monkeypatch.setattr(erased_feed_sweep, "_run", fake_run)
    assert erased_feed_sweep.main(["--dry-run"]) == 0
    assert seen == {"dry_run": True}
    assert json.loads(capsys.readouterr().out)["dry_run"] is True


def test_the_sql_store_selects_rows_by_the_deletion_stamp():
    from meeting_api.collector.adapters import SqlAlchemyTranscriptStore

    src = inspect.getsource(SqlAlchemyTranscriptStore.erased_meeting_ids)
    assert 'has_key("artifact_deletion")' in src
