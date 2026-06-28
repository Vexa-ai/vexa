"""CP2 (docs/CONTROL-PLANE.md §4) — the collector is the SINGLE writer of the native transcript feed
``tc:meeting:{native}`` (P23). Fixture segments in → exact native-keyed wire entries out; a session_end
message in → a session_end marker out. Deterministic: fakeredis + explicit ``ingest`` (same in ⇒ same out).
This is the meetings-domain replacement for the agent relay deleted in the same change.
"""
from __future__ import annotations

import json

import fakeredis.aioredis
import pytest

from meeting_api.collector import ingest
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore


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


def _message(meeting_id, segments):
    return {"payload": json.dumps(
        {"type": "transcription", "meeting_id": str(meeting_id), "segments": segments})}


async def _native_entries(client, native):
    rows = await client.xrange(f"tc:meeting:{native}")
    out = []
    for _id, fields in rows:
        raw = fields.get(b"payload") or fields.get("payload")
        out.append(json.loads(raw.decode() if isinstance(raw, bytes) else raw))
    return out


async def test_cp2_collector_writes_native_feed(store, bus):
    await ingest(store, bus, _message(1, [
        {"segment_id": "a", "start": 1.0, "end": 2.5, "text": "Hello", "speaker": "Alice",
         "language": "en", "completed": True},
        {"segment_id": "b", "start": 2.5, "end": 4.0, "text": "world", "speaker": "Alice",
         "language": "en", "completed": False},
    ]))
    entries = await _native_entries(bus._client, "abc-defg-hij")
    assert [e["segments"][0]["text"] for e in entries] == ["Hello", "world"]
    first = entries[0]
    assert first["session_uid"] == "abc-defg-hij" and first["meeting_id"] == "abc-defg-hij"
    assert first["segments"][0] == {
        "speaker": "Alice", "text": "Hello", "start": 1.0, "end": 2.5, "abs_start_ms": 1000,
        "absolute_start_time": None, "completed": True, "language": "en", "segment_id": "a",
    }


async def test_cp2_session_end_marker_on_native_feed(store, bus):
    await ingest(store, bus, {"payload": json.dumps({"type": "session_end", "meeting_id": "1"})})
    entries = await _native_entries(bus._client, "abc-defg-hij")
    assert entries == [{"type": "session_end", "uid": "abc-defg-hij"}]


async def test_cp2_native_feed_byte_identical_across_runs(store):
    """same fixture in ⇒ byte-identical native entries out (twice)."""
    async def run():
        client = fakeredis.aioredis.FakeRedis()
        await ingest(store, FakeRedisBus(client), _message(1, [
            {"segment_id": "a", "start": 0.0, "end": 1.0, "text": "hi", "speaker": "Jane",
             "language": "en", "completed": True}]))
        entries = await _native_entries(client, "abc-defg-hij")
        await client.aclose()
        return entries

    assert json.dumps(await run(), sort_keys=True) == json.dumps(await run(), sort_keys=True)
