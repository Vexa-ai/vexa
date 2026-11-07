"""A malformed ``transcription_segments`` entry is refused and acknowledged, never raised.

The collector's admission check reads fields any stream writer controls. Whatever shape they take —
a ``sig`` that is not the signer's 64 lowercase hex characters, a missing ``sig``, an ``auth`` that
is not a string, a payload the parser cannot hold — the entry is refused like an unsigned one and
acknowledged with its batch. Raising instead would abort the batch before its ack, and the reclaim
sweep would meet the same entry again on every tick, holding every other entry in it back.
"""
from __future__ import annotations

import json

import fakeredis
import pytest

from meeting_api.collector import consume_segments
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore
from meeting_api.collector.ingest import (
    CONSUMER_GROUP,
    STREAM_NAME,
    _admitted,
    reclaim_segments,
    signed_entry,
)

from _segment_auth import admin_token, signed_fields, signed_xadd, token_for  # noqa: F401


@pytest.fixture
def store():
    s = InMemoryTranscriptStore()
    s.seed_meeting(user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij")   # meeting 1
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


def _malformed(variant: str):
    fields = signed_fields(_data(1, variant))
    sig = fields["sig"]
    if variant == "non-ascii-sig":
        fields["sig"] = "é" + sig[1:]                     # 64 characters, one of them not ASCII
    elif variant == "short-sig":
        fields["sig"] = sig[:-1]
    elif variant == "long-sig":
        fields["sig"] = sig + "0"
    elif variant == "non-hex-sig":
        fields["sig"] = "g" + sig[1:]
    elif variant == "upper-case-sig":
        fields["sig"] = sig.upper()
    elif variant == "missing-sig":
        del fields["sig"]
    elif variant == "int-auth":
        fields["auth"] = 12345
    elif variant == "list-auth":
        fields["auth"] = ["a", "b"]
    elif variant == "unencodable-auth":
        fields["auth"] = "\ud800." + fields["auth"].split(".")[1]
    elif variant == "overflowing-meeting-id":
        # validly signed, but its meeting_id parses as an infinite float
        fields = _signed_raw('{"type":"transcription","meeting_id":1e999}')
    elif variant == "deeply-nested-payload":
        # validly signed, but nested past the parser's recursion limit
        fields = _signed_raw("[" * 100_000 + "]" * 100_000)
    else:
        raise AssertionError(variant)
    return fields


def _signed_raw(payload: str) -> dict:
    return signed_entry(token_for(1), payload)


VARIANTS = ["non-ascii-sig", "short-sig", "long-sig", "non-hex-sig", "upper-case-sig", "missing-sig",
            "int-auth", "list-auth", "unencodable-auth", "overflowing-meeting-id", "deeply-nested-payload"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_a_malformed_entry_is_refused_without_raising(admin_token, variant):
    assert _admitted(_malformed(variant)) is False


@pytest.mark.parametrize("fields", [None, [], "payload", 7])
def test_fields_that_are_not_a_mapping_are_refused_without_raising(admin_token, fields):
    assert _admitted(fields) is False


def test_the_well_formed_entry_beside_them_is_still_admitted(admin_token):
    assert _admitted(signed_fields(_data(1, "ok"))) is True


async def _texts(store):
    doc = await store.get_transcript(7, "google_meet", "abc-defg-hij")
    return sorted(s["text"] for s in (doc or {}).get("segments", []))


async def _pending(bus) -> int:
    return int((await bus._client.xpending(STREAM_NAME, CONSUMER_GROUP))["pending"])


async def _enqueue_batch(bus):
    await signed_xadd(bus, _data(1, "before"))
    await bus._client.xadd(STREAM_NAME, _malformed("non-ascii-sig"))
    await signed_xadd(bus, _data(1, "after"))


async def test_a_malformed_entry_in_a_read_batch_is_acked_and_the_rest_ingested(store, bus, admin_token):
    await _enqueue_batch(bus)
    assert await consume_segments(store, bus) == 2
    assert await _texts(store) == ["after", "before"]
    assert await _pending(bus) == 0, "the refused entry is acknowledged with its batch"
    assert await consume_segments(store, bus) == 0


async def test_a_malformed_entry_in_a_reclaimed_batch_is_acked_and_the_rest_ingested(store, bus, admin_token):
    await _enqueue_batch(bus)
    dead = await bus.read_segments(group=CONSUMER_GROUP, consumer="collector-dead", stream=STREAM_NAME, count=10)
    assert len(dead) == 3 and await _pending(bus) == 3
    assert await reclaim_segments(store, bus, consumer="collector-live", min_idle_ms=0) == 2
    assert await _texts(store) == ["after", "before"]
    assert await _pending(bus) == 0, "the reclaim sweep does not meet the malformed entry again"
    assert await reclaim_segments(store, bus, consumer="collector-live", min_idle_ms=0) == 0


async def test_a_batch_of_only_malformed_entries_is_acked(store, bus, admin_token):
    # Every variant a stream can carry (field values reach Redis as strings; a lone surrogate cannot).
    carried = [v for v in VARIANTS if v != "unencodable-auth"]
    for variant in carried:
        fields = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in _malformed(variant).items()}
        await bus._client.xadd(STREAM_NAME, fields)
    assert await consume_segments(store, bus, count=len(carried)) == 0
    assert await _pending(bus) == 0
    assert await _texts(store) == []
