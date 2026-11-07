"""A ``transcription_segments`` entry is stored only when it is signed by the session that wrote it.

An entry with no ``auth`` and no ``sig`` (the shape a 0.12 bot writes) is refused, whatever the
environment says: ``VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S`` is not read. A signature that does not
verify is refused; a signed entry is stored.
"""
from __future__ import annotations

import json

import fakeredis
import pytest

from meeting_api.collector import consume_segments
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore
from meeting_api.collector.ingest import STREAM_NAME

from _segment_auth import admin_token, signed_fields, signed_xadd  # noqa: F401


@pytest.fixture
def store():
    s = InMemoryTranscriptStore()
    s.seed_meeting(user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij")   # meeting 1
    s.seed_meeting(user_id=8, platform="google_meet", native_meeting_id="xyz-wxyz-xyz")   # meeting 2
    return s


@pytest.fixture
async def bus():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    b = FakeRedisBus(client)
    yield b
    await client.aclose()


def _data(meeting_id, text):
    return {"type": "transcription", "meeting_id": meeting_id,
            "segments": [{"segment_id": f"s-{text}", "start": 0.0, "end": 1.0, "text": text, "completed": True}]}


async def _xadd_0_12(bus, data):
    """The entry exactly as a 0.12.28 bot writes it: the payload and nothing else."""
    await bus._client.xadd(STREAM_NAME, {"payload": json.dumps(data)})


async def _texts(store, user, native):
    doc = await store.get_transcript(user, "google_meet", native)
    return sorted(s["text"] for s in (doc or {}).get("segments", []))


@pytest.mark.parametrize("old_setting", [None, "14400"])
async def test_an_unsigned_entry_is_refused(store, bus, admin_token, monkeypatch, old_setting):
    if old_setting:
        monkeypatch.setenv("VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S", old_setting)
    await _xadd_0_12(bus, _data(1, "unsigned"))
    assert await consume_segments(store, bus) == 0
    assert await _texts(store, 7, "abc-defg-hij") == []


async def test_a_bad_signature_is_refused(store, bus, admin_token):
    fields = signed_fields(_data(1, "x"))
    fields["payload"] = json.dumps(_data(1, "tampered"))
    await bus._client.xadd(STREAM_NAME, fields)
    assert await consume_segments(store, bus) == 0


async def test_a_signed_entry_is_stored(store, bus, admin_token):
    await signed_xadd(bus, _data(1, "signed"))
    assert await consume_segments(store, bus) == 1
    assert await _texts(store, 7, "abc-defg-hij") == ["signed"]
