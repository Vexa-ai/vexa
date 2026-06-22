"""Segment-ingestion eval — stream → store → publish ``tc:meeting:{id}:mutable``, driven
deterministically by fakeredis (no background loop: the test calls ``ingest`` /
``consume_segments`` explicitly, like the runtime scheduler's ``tick()``).

Asserts:
  * one stream message's segments are persisted to the store and then READABLE through the
    sealed-api ``get_transcript`` path (round-trip: ingest ⇒ transcript);
  * a ``:mutable`` update is published on the EXACT channel the gateway ``/ws`` subscribes to
    (``tc:meeting:{id}:mutable``) with the bot's live payload shape;
  * malformed segments (missing segment_id / zero-length / inverted) are filtered;
  * ``consume_segments`` drains a fakeredis stream batch via XREADGROUP + XACK.
"""
from __future__ import annotations

import json

import fakeredis.aioredis
import pytest

from meeting_api.collector import consume_segments, ingest
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore
from meeting_api.collector.ingest import STREAM_NAME, _mutable_channel


@pytest.fixture
def store():
    s = InMemoryTranscriptStore()
    s.seed_meeting(user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij")
    return s


@pytest.fixture
async def bus():
    client = fakeredis.aioredis.FakeRedis()
    b = FakeRedisBus(client)
    yield b
    await client.aclose()


def _message(meeting_id: int, segments: list[dict]) -> dict:
    """A decoded ``transcription_segments`` stream message (the ``payload`` field is the JSON)."""
    return {"payload": json.dumps({
        "type": "transcription", "meeting_id": str(meeting_id), "uid": "sess-1",
        "platform": "google_meet", "segments": segments,
    })}


async def test_ingest_persists_and_is_readable(store, bus):
    n = await ingest(store, bus, _message(1, [
        {"segment_id": "ch-0:1:a", "start": 1.0, "end": 2.5, "text": "Hello", "language": "en",
         "speaker": "Alice", "completed": True},
        {"segment_id": "ch-0:1:b", "start": 2.5, "end": 4.0, "text": "world", "language": "en",
         "speaker": "Alice", "completed": False},
    ]))
    assert n == 2
    # round-trip: the ingested segments are readable through the sealed-api transcript path
    doc = await store.get_transcript(7, "google_meet", "abc-defg-hij")
    assert [s["text"] for s in doc["segments"]] == ["Hello", "world"]


async def test_ingest_publishes_mutable_on_gateway_channel(store, bus):
    await ingest(store, bus, _message(1, [
        {"segment_id": "ch-0:1:a", "start": 1.0, "end": 2.5, "text": "Hello", "language": "en",
         "speaker": "Alice", "completed": True},
    ]))
    assert len(bus.published) == 1
    channel, raw = bus.published[0]
    assert channel == _mutable_channel(1) == "tc:meeting:1:mutable"
    payload = json.loads(raw)
    # the bot's live-path shape the dashboard consumes
    assert payload["type"] == "transcript"
    assert payload["meeting"] == {"id": 1}
    assert payload["speaker"] == "Alice"
    assert len(payload["confirmed"]) == 1 and payload["pending"] == []


async def test_ingest_filters_malformed_segments(store, bus):
    n = await ingest(store, bus, _message(1, [
        {"start": 1.0, "end": 2.0, "text": "no id"},                       # missing segment_id
        {"segment_id": "z", "start": 5.0, "end": 5.0, "text": "zero final",  # zero-length COMPLETED → drop
         "completed": True},
        {"segment_id": "ok", "start": 1.0, "end": 2.0, "text": "kept", "completed": True},
    ]))
    assert n == 1
    doc = await store.get_transcript(7, "google_meet", "abc-defg-hij")
    assert [s["text"] for s in doc["segments"]] == ["kept"]


async def test_ingest_keeps_zero_length_pending_draft(store, bus):
    """A live DRAFT (completed=False) has no end yet — `start == end` is its placeholder. It MUST be
    ingested + published as `pending` (it's the live in-progress text the dashboard renders), unlike a
    zero-length COMPLETED segment which is garbage and dropped."""
    n = await ingest(store, bus, _message(1, [
        {"segment_id": "ch-0:1:draft", "start": 5.0, "end": 5.0, "text": "being spoken",
         "speaker": "Alice", "completed": False},
    ]))
    assert n == 1, "a zero-duration pending draft must be kept, not filtered"
    channel, raw = bus.published[-1]
    payload = json.loads(raw)
    assert payload["pending"] and payload["pending"][0]["text"] == "being spoken"
    assert payload["confirmed"] == []


async def test_ingest_corrects_inverted_timestamps(store, bus):
    await ingest(store, bus, _message(1, [
        {"segment_id": "inv", "start": 4.0, "end": 1.0, "text": "swapped", "completed": True},
    ]))
    doc = await store.get_transcript(7, "google_meet", "abc-defg-hij")
    seg = doc["segments"][0]
    assert seg["start"] == 1.0 and seg["end"] == 4.0


async def test_ingest_ignores_non_segment_messages(store, bus):
    assert await ingest(store, bus, {"payload": json.dumps({"type": "session_start", "uid": "s"})}) == 0
    assert await ingest(store, bus, {}) == 0
    assert await ingest(store, bus, {"payload": "not-json{"}) == 0


async def test_consume_segments_drains_a_fakeredis_batch(store, bus):
    # enqueue two stream messages, then drain the batch via XREADGROUP + XACK
    await bus.xadd(STREAM_NAME, {"type": "transcription", "meeting_id": "1",
                                 "segments": [{"segment_id": "a", "start": 0.0, "end": 1.0,
                                               "text": "one", "completed": True}]})
    await bus.xadd(STREAM_NAME, {"type": "transcription", "meeting_id": "1",
                                 "segments": [{"segment_id": "b", "start": 1.0, "end": 2.0,
                                               "text": "two", "completed": True}]})
    total = await consume_segments(store, bus)
    assert total == 2
    doc = await store.get_transcript(7, "google_meet", "abc-defg-hij")
    assert {s["text"] for s in doc["segments"]} == {"one", "two"}
    # acked: a second drain reads nothing new
    assert await consume_segments(store, bus) == 0
