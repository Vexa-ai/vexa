"""A bot may write segments only for its own meeting.

Every bot appends to one ``transcription_segments`` stream, so the collector admits an entry only
when its session signed it: ``auth`` (the MeetingToken's header.payload) and ``sig`` (HMAC-SHA256 of
the payload keyed with the token), with the payload's ``meeting_id`` the token's. Anything else —
unsigned, signed for another meeting, tampered, signed with a token another secret minted, or with an
expired token — is acknowledged and dropped, on both the read and the reclaim path.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import fakeredis
import pytest

from meeting_api.collector import consume_segments
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore
from meeting_api.collector.ingest import CONSUMER_GROUP, STREAM_NAME, _admitted, reclaim_segments

from _segment_auth import admin_token, signed_fields, signed_xadd, token_for  # noqa: F401


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


def _data(meeting_id, text="hello"):
    return {"type": "transcription", "meeting_id": meeting_id,
            "segments": [{"segment_id": f"s-{text}", "start": 0.0, "end": 1.0, "text": text, "completed": True}]}


async def _texts(store, user, native):
    doc = await store.get_transcript(user, "google_meet", native)
    return sorted(s["text"] for s in (doc or {}).get("segments", []))


async def test_an_entry_signed_by_its_own_session_is_ingested(store, bus, admin_token):
    await signed_xadd(bus, _data(1, "mine"))
    assert await consume_segments(store, bus) == 1
    assert await _texts(store, 7, "abc-defg-hij") == ["mine"]


async def test_a_bot_cannot_write_another_meetings_transcript(store, bus, admin_token):
    # meeting 1's session signs an entry that names meeting 2
    await signed_xadd(bus, _data(2, "forged"), token=token_for(1))
    assert await consume_segments(store, bus) == 0
    assert await _texts(store, 8, "xyz-wxyz-xyz") == []
    # and it is acknowledged, not left to be read again
    assert await consume_segments(store, bus) == 0


@pytest.mark.parametrize("variant", ["unsigned", "tampered", "foreign-secret", "expired", "auth-of-another",
                                     "no-secret-configured"])
async def test_every_other_entry_is_dropped(store, bus, admin_token, monkeypatch, variant):
    data = _data(1, variant)
    if variant == "unsigned":
        fields = {"payload": json.dumps(data)}
    elif variant == "tampered":
        fields = signed_fields(data)
        fields["payload"] = json.dumps(_data(1, "changed"))
    elif variant == "foreign-secret":
        fields = signed_fields(data, token=token_for(1, secret="another-secret-0123456789abcdef0123"))
    elif variant == "expired":
        fields = signed_fields(data, token=token_for(1, ttl_seconds=-10))
    elif variant == "auth-of-another":
        fields = signed_fields(data)
        fields["auth"] = ".".join(token_for(2).split(".")[:2])
    else:
        fields = signed_fields(data)
        monkeypatch.delenv("ADMIN_TOKEN")
    assert _admitted(fields) is False
    await bus._client.xadd(STREAM_NAME, fields)
    assert await consume_segments(store, bus) == 0
    assert await _texts(store, 7, "abc-defg-hij") == []


async def test_the_reclaim_path_admits_the_same_way(store, bus, admin_token):
    await signed_xadd(bus, _data(2, "forged"), token=token_for(1))
    await signed_xadd(bus, _data(1, "mine"))
    dead = await bus.read_segments(group=CONSUMER_GROUP, consumer="collector-dead", stream=STREAM_NAME, count=10)
    assert len(dead) == 2
    assert await reclaim_segments(store, bus, consumer="collector-live", min_idle_ms=0) == 1
    assert await _texts(store, 7, "abc-defg-hij") == ["mine"]
    assert await _texts(store, 8, "xyz-wxyz-xyz") == []


def test_the_signature_matches_the_bots_vector():
    """The bot's sink pins the same vector (transcript-redis.test.ts)."""
    token = "eyJhbGciOiJIUzI1NiJ9.eyJtZWV0aW5nX2lkIjo0Mn0.c2lnbmF0dXJl"
    sig = hmac.new(token.encode(), b'{"type":"transcription","meeting_id":42}', hashlib.sha256).hexdigest()
    assert sig == "ea8b616bd36e85dbc3f3c5aeb36f7bc2391f6a759d425355abfef2430516963d"
