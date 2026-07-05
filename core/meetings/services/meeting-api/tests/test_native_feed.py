"""CP2 (docs/CONTROL-PLANE.md §4) — the collector is the SINGLE writer of the per-meeting transcript feed
``tc:meeting:{meeting_id}`` (P23). P0 (cross-tenant leak fix): the feed keys on the meetings-domain numeric
ROW id, NOT the native meeting id — the native id collides across DIFFERENT users of the same link (leak)
and across ONE user's re-sends (wrong-row). The native id still rides IN the wire payload for display.
Fixture segments in → exact ROW-keyed wire entries out; a session_end message in → a session_end marker
out on the ROW-keyed feed. Deterministic: fakeredis + explicit ``ingest`` (same in ⇒ same out).
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


async def _feed_entries(client, meeting_id):
    rows = await client.xrange(f"tc:meeting:{meeting_id}")
    out = []
    for _id, fields in rows:
        raw = fields.get(b"payload") or fields.get("payload")
        out.append(json.loads(raw.decode() if isinstance(raw, bytes) else raw))
    return out


async def test_cp2_collector_writes_row_keyed_feed(store, bus):
    await ingest(store, bus, _message(1, [
        {"segment_id": "a", "start": 1.0, "end": 2.5, "text": "Hello", "speaker": "Alice",
         "language": "en", "completed": True},
        {"segment_id": "b", "start": 2.5, "end": 4.0, "text": "world", "speaker": "Alice",
         "language": "en", "completed": False},
    ]))
    # P0: the CARRIER keys on the numeric ROW id (1), NOT the native code.
    assert await _feed_entries(bus._client, "abc-defg-hij") == []      # native-keyed feed is NOT written
    entries = await _feed_entries(bus._client, 1)                      # row-keyed feed IS
    assert [e["segments"][0]["text"] for e in entries] == ["Hello", "world"]
    first = entries[0]
    # The wire DISPLAY fields still carry the native (readability) even though the KEY is the row id.
    assert first["session_uid"] == "abc-defg-hij" and first["meeting_id"] == "abc-defg-hij"
    assert first["segments"][0] == {
        "speaker": "Alice", "text": "Hello", "start": 1.0, "end": 2.5, "abs_start_ms": 1000,
        "absolute_start_time": None, "completed": True, "language": "en", "segment_id": "a",
    }


async def test_cp2_session_end_marker_on_row_keyed_feed(store, bus):
    # session_end carries the native for display but keys the marker on the numeric ROW id.
    await ingest(store, bus, {"payload": json.dumps(
        {"type": "session_end", "meeting_id": "1", "native_meeting_id": "abc-defg-hij"})})
    assert await _feed_entries(bus._client, "abc-defg-hij") == []          # not on the native key
    entries = await _feed_entries(bus._client, 1)                          # on the row key
    assert entries == [{"type": "session_end", "uid": "abc-defg-hij"}]


async def test_cp2_native_feed_byte_identical_across_runs(store):
    """same fixture in ⇒ byte-identical native entries out (twice)."""
    async def run():
        client = fakeredis.aioredis.FakeRedis()
        await ingest(store, FakeRedisBus(client), _message(1, [
            {"segment_id": "a", "start": 0.0, "end": 1.0, "text": "hi", "speaker": "Jane",
             "language": "en", "completed": True}]))
        entries = await _feed_entries(client, 1)
        await client.aclose()
        return entries

    assert json.dumps(await run(), sort_keys=True) == json.dumps(await run(), sort_keys=True)


async def test_collector_writes_row_keyed_feed_without_store_lookup(bus):
    """P0/P23: the collector writes tc:meeting:{row_id} from the ROW id alone — no store lookup needed
    (the row id is always in the segment envelope). The stamped native rides in the wire for display."""
    store = InMemoryTranscriptStore()  # NO seed_meeting → store.native_for() would miss (irrelevant now)
    msg = {"payload": json.dumps({
        "type": "transcription", "meeting_id": "1", "native_meeting_id": "gdv-ffkx-vdc",
        "platform": "google_meet",
        "segments": [{"segment_id": "a", "start": 1.0, "end": 2.0, "text": "hi", "speaker": "Dmitriy",
                      "language": "en", "completed": True}],
    })}
    n = await ingest(store, bus, msg)
    assert n == 1
    assert await _feed_entries(bus._client, "gdv-ffkx-vdc") == []      # NOT native-keyed
    entries = await _feed_entries(bus._client, 1)                      # row-keyed
    assert [e["segments"][0]["text"] for e in entries] == ["hi"]
    assert entries[0]["session_uid"] == "gdv-ffkx-vdc"                 # native rides in the wire (display)
