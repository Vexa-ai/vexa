"""Ingest refuses every message for a meeting whose transcript its owner deleted.

The bot keeps a stream of segments in flight, so one can arrive after the owner deleted the
meeting's transcript. Ingest checks the meeting row's deletion stamp first: a refused message
re-creates nothing — no live hash, no `active_meetings` entry, no `tc:meeting:{id}` feed, and no
`transcriptions` rows through the db-writer.

Offline: the in-memory store in production topology (fakeredis hash + feed), no Postgres.
"""
from __future__ import annotations

import inspect
import json

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.collector import ingest
from meeting_api.collector.db_writer import ACTIVE_MEETINGS_KEY, flush_meeting_segments, segments_hash_key
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore
from meeting_api.collector.ingest import _transcript_stream
from meeting_api.recordings.fakes import InMemoryStorage

OWNER, MEETING_ID, OTHER_MEETING = 7, 41, 42
DELETED = {"state": "completed", "scope": "primary_transcript_recording_and_fixture_storage"}
PENDING = {"state": "pending", "scope": "primary_transcript_recording_and_fixture_storage"}


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture
def sync(server):
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture
def store(server):
    s = InMemoryTranscriptStore(redis_client=fakeredis.aioredis.FakeRedis(server=server))
    s.seed_meeting(meeting_id=MEETING_ID, user_id=OWNER, platform="google_meet",
                   native_meeting_id="private-room", status="completed")
    s.seed_meeting(meeting_id=OTHER_MEETING, user_id=OWNER, platform="google_meet",
                   native_meeting_id="other-room", status="active")
    return s


@pytest.fixture
async def bus(server):
    client = fakeredis.aioredis.FakeRedis(server=server)
    yield FakeRedisBus(client)
    await client.aclose()


def _segments(meeting_id: int) -> dict:
    return {"payload": json.dumps({
        "type": "transcription", "meeting_id": str(meeting_id), "platform": "google_meet",
        "segments": [{"segment_id": "late:1", "start": 1.0, "end": 2.0, "text": "a late segment",
                      "language": "en", "speaker": "Ana", "completed": True}],
    })}


def _marker(meeting_id: int, kind: str) -> dict:
    body = {"type": kind, "meeting_id": str(meeting_id)}
    if kind == "transcript_retract":
        body["segment_ids"] = ["late:1"]
    return {"payload": json.dumps(body)}


def _stamp(store, deletion: dict) -> None:
    store._meetings[MEETING_ID]["data"]["artifact_deletion"] = dict(deletion)


def _recreated(sync, meeting_id: int) -> list[str]:
    keys = [segments_hash_key(meeting_id), _transcript_stream(meeting_id)]
    out = [k for k in keys if sync.exists(k)]
    if sync.sismember(ACTIVE_MEETINGS_KEY, str(meeting_id)):
        out.append(ACTIVE_MEETINGS_KEY)
    return out


@pytest.mark.parametrize("deletion", [DELETED, PENDING], ids=["deleted", "deletion-pending"])
async def test_segments_for_a_deleted_meeting_are_refused(store, bus, sync, deletion):
    _stamp(store, deletion)
    assert await ingest(store, bus, _segments(MEETING_ID)) == 0
    assert _recreated(sync, MEETING_ID) == []
    assert bus.published == []


@pytest.mark.parametrize("kind", ["session_end", "transcript_retract"])
async def test_feed_markers_for_a_deleted_meeting_are_refused(store, bus, sync, kind):
    _stamp(store, DELETED)
    assert await ingest(store, bus, _marker(MEETING_ID, kind)) == 0
    assert not sync.exists(_transcript_stream(MEETING_ID))
    assert bus.published == []


async def test_a_meeting_that_was_not_deleted_still_ingests(store, bus, sync):
    _stamp(store, DELETED)
    assert await ingest(store, bus, _segments(OTHER_MEETING)) == 1
    assert sync.exists(segments_hash_key(OTHER_MEETING))
    assert sync.exists(_transcript_stream(OTHER_MEETING))


def test_a_segment_after_the_delete_route_recreates_nothing(store, server, sync):
    client = TestClient(create_app(transcript_store=store, storage=InMemoryStorage()),
                        raise_server_exceptions=False)
    assert client.delete(f"/meetings/{MEETING_ID}", headers={"x-user-id": str(OWNER)}).status_code == 204

    import asyncio

    async def late():
        async_client = fakeredis.aioredis.FakeRedis(server=server)
        try:
            return await ingest(store, FakeRedisBus(async_client), _segments(MEETING_ID))
        finally:
            await async_client.aclose()

    assert asyncio.run(late()) == 0
    assert _recreated(sync, MEETING_ID) == []
    assert store._meetings[MEETING_ID]["segments"] == {}


async def test_the_db_writer_drops_a_deleted_meetings_hash_without_writing_rows(store, sync):
    """A segment that reached the hash in the instant before the delete is not flushed back."""
    _stamp(store, DELETED)
    sync.hset(segments_hash_key(MEETING_ID), "late:1", json.dumps(
        {"segment_id": "late:1", "start": 1.0, "end": 2.0, "text": "a late segment"}))
    sync.sadd(ACTIVE_MEETINGS_KEY, str(MEETING_ID))

    assert await flush_meeting_segments(store._redis, store, MEETING_ID, immutability_threshold=0) == 0
    assert store._meetings[MEETING_ID]["segments"] == {}
    assert _recreated(sync, MEETING_ID) == []


async def test_the_erased_answer_reads_the_deletion_stamp(store):
    assert not await store.transcript_erased(MEETING_ID)
    _stamp(store, PENDING)
    assert await store.transcript_erased(MEETING_ID)
    assert not await store.transcript_erased(OTHER_MEETING)
    assert not await store.transcript_erased(9999)


def test_the_sql_store_reads_the_same_stamp():
    """The production store needs a database this suite does not start; pin that it answers from
    the row's deletion stamp, the field every transcript reader checks."""
    from meeting_api.collector.adapters import SqlAlchemyTranscriptStore

    src = inspect.getsource(SqlAlchemyTranscriptStore.transcript_erased)
    assert "artifact_deletion" in src
