"""#508 · A3 direct coverage of the REFACTORED SqlAlchemyTranscriptStore (not the in-memory fake).

The collector fake-lane exercises ``InMemoryTranscriptStore``; this pins the actual changed code —
``SqlAlchemyTranscriptStore._merge_live_segments`` (the post-session half that assembles the
response and merges the live Redis hash). It needs no SQLAlchemy (the DB half ``_transcript_pg_part``
is gated by test_tx_scope + the live A2/A3 probes); it drives the merge with a redis stub, so a
regression in field set / key order / sort / absolute-time derivation / the Redis merge is caught
offline. The Redis-only-segment assertion IS the negative control: delete the post-session merge and
``s-redis`` disappears → this test goes red (the same guard the issue names at adapters.py:237-239).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from meeting_api.collector.adapters import SqlAlchemyTranscriptStore, _segment_to_api

START = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
CREATED = datetime(2026, 7, 14, 11, 59, 0, tzinfo=timezone.utc)

# The exact key set + order _merge_live_segments must emit (byte-identical to the pre-split build).
EXPECTED_KEYS = ["id", "platform", "native_meeting_id", "constructed_meeting_url", "status",
                 "start_time", "end_time", "recordings", "notes", "data", "segments"]


class _RedisStub:
    def __init__(self, hash_map):
        self._hash_map = hash_map

    async def hgetall(self, key):
        return self._hash_map.get(key, {})


def _snap():
    return {
        "id": 5, "platform": "google_meet", "platform_specific_id": "abc-defg-hij",
        "status": "active", "start_time": START, "end_time": None, "created_at": CREATED,
        "data": {"constructed_meeting_url": "https://meet.google.com/abc-defg-hij",
                 "recordings": [{"id": "r1"}], "notes": "hi"},
    }


def _pg_part():
    """One persisted (Postgres) segment, as _transcript_pg_part would have built it."""
    s = _segment_to_api({"start": 5.0, "end": 6.0, "text": "persisted", "segment_id": "s-pg",
                         "completed": True})
    return {"s-pg": s}, ["s-pg"]


async def test_merge_assembles_response_and_merges_live_redis():
    seg_by_id, order = _pg_part()
    redis = _RedisStub({"meeting:5:segments": {
        "s-redis": json.dumps({"start": 1.0, "end": 2.0, "text": "in-flight", "segment_id": "s-redis"})}})
    store = SqlAlchemyTranscriptStore(session_factory=None, redis_client=redis)

    doc = await store._merge_live_segments((_snap(), seg_by_id, order))

    # Top-level shape: exact keys, exact order.
    assert list(doc.keys()) == EXPECTED_KEYS
    assert doc["id"] == 5 and doc["platform"] == "google_meet"
    assert doc["native_meeting_id"] == "abc-defg-hij"
    assert doc["status"] == "active"
    # start_time is serialized as UTC ISO with a Z marker (clients parse it as UTC, render local).
    assert doc["start_time"] == START.isoformat().replace("+00:00", "Z") and doc["end_time"] is None
    assert doc["notes"] == "hi" and doc["recordings"] == [{"id": "r1"}]
    # The live Redis-only segment is merged in AND sorted before the later persisted one.
    ids = [s["segment_id"] for s in doc["segments"]]
    assert ids == ["s-redis", "s-pg"], "redis-only in-flight segment must be merged and sorted by start"
    # absolute_start_time derived for every segment (the dashboard skips segments without it).
    assert all(s.get("absolute_start_time") for s in doc["segments"])


async def test_merge_without_redis_returns_persisted_only():
    """redis_client=None (or an empty hash) → the persisted segments still assemble cleanly, no crash."""
    seg_by_id, order = _pg_part()
    store = SqlAlchemyTranscriptStore(session_factory=None, redis_client=None)
    doc = await store._merge_live_segments((_snap(), seg_by_id, order))
    assert [s["segment_id"] for s in doc["segments"]] == ["s-pg"]
    assert list(doc.keys()) == EXPECTED_KEYS
