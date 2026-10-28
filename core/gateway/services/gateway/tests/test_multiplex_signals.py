"""Transcript SIGNALS on `/ws` (ADR transcript signals, part C): the annotations the signals service
writes to `sig:meeting:{row}` reach the terminal the way the live transcript reaches a `/ws` client —
through a subscription the meeting-api authorize hop granted, for the ROW it granted.

Same access rule as the transcript: only a caller who may read that meeting's transcript gets its
annotations. A non-participant gets none; another tenant's row with the same native id is never read.
"""
from __future__ import annotations

import asyncio
import json

import gateway.multiplex as mux
from gateway.multiplex import _run_multiplex
from conftest import FakeAuthorizer, FakeRedis
from test_multiplex import API_KEY, _WS

ROOM = {"platform": "google_meet", "native_id": "room-1"}
SUB_SIGNALS = {"action": "subscribe", "meetings": [ROOM], "streams": ["signals"]}
ANNOTATION = {"segment_id": "s-1", "scores": {"decision": 0.91, "blocker": 0.1},
              "words": {"Friday": 0.8, "the": 0.01}, "model": "jev-1.13", "brief_version": 3,
              "at": 1800000000.0}


async def _settle(n=40):
    for _ in range(n):
        await asyncio.sleep(0)


def _fast(monkeypatch):
    monkeypatch.setattr(mux, "WS_REAUTH_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(mux, "SIGNALS_BLOCK_MS", 20)


def _signals(ws):
    return [f for f in ws.sent if f.get("type") == "meeting.signals"]


def _annotations(ws):
    return [a for f in _signals(ws) for a in f.get("annotations", [])]


async def _write(redis, row, ann=ANNOTATION):
    await redis.xadd(f"sig:meeting:{row}", {"payload": json.dumps(ann)})


async def _open(redis, auth, inbound):
    ws = _WS(inbound=inbound, api_key=API_KEY, close_when_drained=False)
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    await _settle()
    return ws, task


async def _close(ws, task):
    ws.disconnect()
    await asyncio.wait_for(task, 2)


async def test_a_participant_gets_the_backlog_then_live_annotations(monkeypatch):
    _fast(monkeypatch)
    redis = FakeRedis()
    await _write(redis, 42, {**ANNOTATION, "segment_id": "s-0"})          # written before subscribe
    auth = FakeAuthorizer(valid_key=API_KEY, auth_map={("google_meet", "room-1"): {"meeting_id": 42, "user_id": 7}})
    ws, task = await _open(redis, auth, [SUB_SIGNALS])
    assert any(f.get("type") == "subscribed" and f["meetings"] == [ROOM] for f in ws.sent), ws.sent
    await _write(redis, 42)
    await asyncio.sleep(0.1)
    anns = _annotations(ws)
    assert [a["segment_id"] for a in anns] == ["s-0", "s-1"], anns
    first = _signals(ws)[0]
    assert first["meeting"] == {"id": "42", "platform": "google_meet", "native_id": "room-1"}
    a = anns[1]
    assert a["scores"] == {"decision": 0.91, "blocker": 0.1}
    assert a["words"]["Friday"] == 0.8 and a["model"] == "jev-1.13" and a["brief_version"] == 3
    assert a["id"], "each annotation carries its stream id, so a reconnect can drop duplicates"
    await _close(ws, task)


async def test_signals_only_subscription_does_not_double_the_transcript(monkeypatch):
    """The terminal already reads the transcript over its own feed: a signals-only subscription
    fans in the annotations and nothing else."""
    _fast(monkeypatch)
    redis = FakeRedis()
    auth = FakeAuthorizer(valid_key=API_KEY, auth_map={("google_meet", "room-1"): {"meeting_id": 42, "user_id": 7}})
    ws, task = await _open(redis, auth, [SUB_SIGNALS])
    await redis.publish("tc:meeting:42:mutable", json.dumps({"type": "transcription_segment", "text": "x"}))
    await _settle()
    assert not any(f.get("type") == "transcription_segment" for f in ws.sent)
    await _close(ws, task)


async def test_a_plain_subscribe_is_unchanged_and_carries_no_signals(monkeypatch):
    """ws.v1 clients that never asked for signals see exactly the 0.12 frames."""
    _fast(monkeypatch)
    redis = FakeRedis()
    await _write(redis, 42)
    auth = FakeAuthorizer(valid_key=API_KEY, auth_map={("google_meet", "room-1"): {"meeting_id": 42, "user_id": 7}})
    ws, task = await _open(redis, auth, [{"action": "subscribe", "meetings": [ROOM]}])
    await redis.publish("tc:meeting:42:mutable", json.dumps({"type": "transcription_segment", "text": "x"}))
    await asyncio.sleep(0.05)
    assert any(f.get("text") == "x" for f in ws.sent)
    assert not _signals(ws)
    assert "sig:meeting:42" not in redis.xread_keys
    await _close(ws, task)


async def test_deny_a_non_participant_reads_no_annotations(monkeypatch):
    """DENY: a key the authorize hop grants nothing for this meeting gets an error and no frames,
    and the gateway never even opens the meeting's signals stream."""
    _fast(monkeypatch)
    redis = FakeRedis()
    await _write(redis, 42)
    auth = FakeAuthorizer(valid_key=API_KEY, auth_map={})                # not a participant
    ws, task = await _open(redis, auth, [SUB_SIGNALS])
    await _write(redis, 42)
    await asyncio.sleep(0.05)
    assert not _signals(ws)
    assert any(f.get("type") == "error" for f in ws.sent), ws.sent
    assert not any(k.startswith("sig:meeting:") for k in redis.xread_keys), redis.xread_keys
    await _close(ws, task)


async def test_deny_another_tenants_row_with_the_same_native_id_is_never_read(monkeypatch):
    """DENY: a recurring meeting code names rows in several tenants. The caller is granted ROW 42;
    tenant B's row 99 with the same native id must never stream to them."""
    _fast(monkeypatch)
    redis = FakeRedis()
    await _write(redis, 99, {**ANNOTATION, "segment_id": "other-tenant"})
    auth = FakeAuthorizer(valid_key=API_KEY, auth_map={("google_meet", "room-1"): {"meeting_id": 42, "user_id": 7}})
    ws, task = await _open(redis, auth, [SUB_SIGNALS])
    await _write(redis, 99, {**ANNOTATION, "segment_id": "other-tenant-live"})
    await _write(redis, 42, {**ANNOTATION, "segment_id": "mine"})
    await asyncio.sleep(0.1)
    ids = [a["segment_id"] for a in _annotations(ws)]
    assert ids == ["mine"], ids
    assert "sig:meeting:99" not in redis.xread_keys
    await _close(ws, task)


async def test_deny_a_reader_removed_mid_meeting_stops_receiving_annotations(monkeypatch):
    _fast(monkeypatch)
    redis = FakeRedis()
    auth_map = {("google_meet", "room-1"): {"meeting_id": 42, "user_id": 7}}
    auth = FakeAuthorizer(valid_key=API_KEY, auth_map=auth_map)
    ws, task = await _open(redis, auth, [SUB_SIGNALS])
    await _write(redis, 42, {**ANNOTATION, "segment_id": "before"})
    await asyncio.sleep(0.05)
    auth_map.clear()                                                     # the owner removes them
    await asyncio.sleep(0.1)
    assert any(f.get("error") == "subscription_revoked" for f in ws.sent)
    await _write(redis, 42, {**ANNOTATION, "segment_id": "after"})
    await asyncio.sleep(0.1)
    ids = [a["segment_id"] for a in _annotations(ws)]
    assert "before" in ids and "after" not in ids, ids
    await _close(ws, task)


async def test_deny_a_key_that_may_not_read_transcripts_gets_no_annotations(monkeypatch):
    """Signals ride the transcript's scope: strict mode refuses a key without it, before the hop."""
    _fast(monkeypatch)
    redis = FakeRedis()
    await _write(redis, 42)
    auth = FakeAuthorizer(user={"user_id": 7, "scopes": ["bot"], "max_concurrent": 3}, valid_key=API_KEY,
                          auth_map={("google_meet", "room-1"): {"meeting_id": 42, "user_id": 7}})
    ws, task = await _open(redis, auth, [SUB_SIGNALS])
    await asyncio.sleep(0.05)
    assert any(f.get("error") == "insufficient_scope" for f in ws.sent), ws.sent
    assert not _signals(ws)
    await _close(ws, task)


async def test_unsubscribe_stops_annotations(monkeypatch):
    _fast(monkeypatch)
    redis = FakeRedis()
    auth = FakeAuthorizer(valid_key=API_KEY, auth_map={("google_meet", "room-1"): {"meeting_id": 42, "user_id": 7}})
    ws, task = await _open(redis, auth, [SUB_SIGNALS, {"action": "unsubscribe", "meetings": [ROOM]}])
    await asyncio.sleep(0.05)
    assert any(f.get("type") == "unsubscribed" for f in ws.sent)
    await _write(redis, 42)
    await asyncio.sleep(0.1)
    assert not _signals(ws)
    await _close(ws, task)


async def test_a_malformed_entry_is_skipped_and_the_stream_goes_on(monkeypatch):
    _fast(monkeypatch)
    redis = FakeRedis()
    auth = FakeAuthorizer(valid_key=API_KEY, auth_map={("google_meet", "room-1"): {"meeting_id": 42, "user_id": 7}})
    ws, task = await _open(redis, auth, [SUB_SIGNALS])
    await redis.xadd("sig:meeting:42", {"payload": "not json{"})
    await redis.xadd("sig:meeting:42", {"payload": json.dumps({"scores": {"decision": 1}})})   # no segment
    await _write(redis, 42)
    await asyncio.sleep(0.1)
    assert [a["segment_id"] for a in _annotations(ws)] == ["s-1"]
    await _close(ws, task)


async def test_flat_stream_fields_are_read_too(monkeypatch):
    """The carrier may hold the annotation as flat fields with JSON values instead of one payload."""
    _fast(monkeypatch)
    redis = FakeRedis()
    auth = FakeAuthorizer(valid_key=API_KEY, auth_map={("google_meet", "room-1"): {"meeting_id": 42, "user_id": 7}})
    ws, task = await _open(redis, auth, [SUB_SIGNALS])
    await redis.xadd("sig:meeting:42", {"segment_id": "s-9", "scores": json.dumps({"action_item": 0.7}),
                                        "words": json.dumps({"invoice": 0.9}), "model": "m", "brief_version": "2",
                                        "at": "1800000001.5"})
    await asyncio.sleep(0.1)
    a = _annotations(ws)[0]
    assert a["segment_id"] == "s-9" and a["scores"] == {"action_item": 0.7} and a["brief_version"] == 2
    await _close(ws, task)


async def test_an_unknown_stream_name_is_refused():
    redis = FakeRedis()
    auth = FakeAuthorizer(valid_key=API_KEY, auth_map={("google_meet", "room-1"): {"meeting_id": 42, "user_id": 7}})
    ws = _WS(inbound=[{"action": "subscribe", "meetings": [ROOM], "streams": ["everything"]}], api_key=API_KEY)
    await _run_multiplex(ws, auth, redis)
    assert any(f.get("error") == "invalid_subscribe_payload" for f in ws.sent), ws.sent
