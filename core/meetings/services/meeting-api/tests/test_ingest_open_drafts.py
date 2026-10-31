"""#1973: the collector's ``:mutable`` bundle carries the speaker's COMPLETE open-draft set.

Replays the 0.13.4 Meet/Zoom reconfirm wire through ``ingest`` with the redis-mode
``InMemoryTranscriptStore`` (the prod topology: live rows in ``meeting:{id}:segments``):
drafts A, B, C (finalised) and D (in progress, zero-length), then A' confirmed under A's id spanning
[A.start, C.end], then ``transcript_retract [B]`` and ``[C]``.
"""
from __future__ import annotations

import json

import fakeredis.aioredis
import pytest

from meeting_api.collector import ingest
from meeting_api.collector.db_writer import segments_hash_key
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore
from meeting_api.collector.ingest import _mutable_channel, _transcript_stream

MID = 1


@pytest.fixture
async def env():
    client = fakeredis.aioredis.FakeRedis()
    store = InMemoryTranscriptStore(redis_client=client)
    store.seed_meeting(user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij")
    yield store, FakeRedisBus(client), client
    await client.aclose()


def _seg(sid, start, end, text, *, speaker="Alice", key="k1", completed=False, source=None):
    s = {"segment_id": sid, "start": start, "end": end, "text": text, "language": "en",
         "speaker": speaker, "speaker_key": key, "completed": completed}
    if source:
        s["source"] = source
    return s


def _msg(segments):
    return {"payload": json.dumps({"type": "transcription", "meeting_id": str(MID), "uid": "s",
                                   "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                                   "segments": segments})}


def _retract(ids):
    return {"payload": json.dumps({"type": "transcript_retract", "meeting_id": str(MID), "segment_ids": ids})}


def _wire(speaker="Alice", key="k1"):
    p = f"{key}:"
    A = _seg(p + "0", 0.0, 3.0, "To est vse davayte.", speaker=speaker, key=key)
    B = _seg(p + "3000", 3.0, 6.0, "12, 14, 15, 6.", speaker=speaker, key=key)
    C = _seg(p + "6000", 6.0, 9.0, "Vot sadites.", speaker=speaker, key=key)
    D = _seg(p + "9000", 9.0, 9.0, "and now", speaker=speaker, key=key)
    A2 = _seg(p + "0", 0.0, 9.0, "Let us show him how it works.", speaker=speaker, key=key, completed=True)
    D2 = _seg(p + "9000", 9.0, 9.0, "and now we see", speaker=speaker, key=key)
    return A, B, C, D, A2, D2


def _frames(bus, start=0):
    return [json.loads(raw) for ch, raw in bus.published[start:] if ch == _mutable_channel(MID)]


def _ids(rows):
    return [r["segment_id"] for r in rows]


async def _tc(client):
    out = []
    for _id, fields in await client.xrange(_transcript_stream(MID)):
        out.append(json.loads(fields[b"payload"]))
    return out


async def _replay(store, bus, segs, interleave=None):
    A, B, C, D, A2, _ = segs
    for s in (A, B, C, D, A2):
        await ingest(store, bus, _msg([s]))
    if interleave == "after_a2":
        await ingest(store, bus, _msg([segs[5]]))
    await ingest(store, bus, _retract([B["segment_id"]]))
    if interleave == "between":
        await ingest(store, bus, _msg([segs[5]]))
    await ingest(store, bus, _retract([C["segment_id"]]))


@pytest.mark.parametrize("speaker", ["Alice", ""])
async def test_replay_bundles_carry_complete_open_set(env, speaker):
    store, bus, client = env
    segs = _wire(speaker=speaker)
    A, B, C, D, A2, _ = segs
    await _replay(store, bus, segs)
    f = _frames(bus)
    kinds = [x["type"] for x in f]
    assert kinds == ["transcript"] * 5 + ["transcript_retract", "transcript", "transcript_retract", "transcript"]
    assert all(x["speaker"] == speaker for x in f if x["type"] == "transcript")
    a, b, c, d = (s["segment_id"] for s in (A, B, C, D))
    assert [_ids(x["pending"]) for x in f[:4]] == [[a], [a, b], [a, b, c], [a, b, c, d]]
    assert _ids(f[4]["confirmed"]) == [a] and f[4]["confirmed"][0]["text"] == A2["text"]
    assert _ids(f[4]["pending"]) == [d]
    assert f[5]["segment_ids"] == [b] and f[6]["confirmed"] == [] and _ids(f[6]["pending"]) == [d]
    assert f[7]["segment_ids"] == [c] and f[8]["confirmed"] == [] and _ids(f[8]["pending"]) == [d]


@pytest.mark.parametrize("where", ["after_a2", "between"])
async def test_interleaved_refined_draft_gets_only_itself(env, where):
    store, bus, _ = env
    segs = _wire()
    D2 = segs[5]
    await _replay(store, bus, segs, interleave=where)
    f = [x for x in _frames(bus) if x["type"] == "transcript"]
    d2 = [x for x in f if any(p["text"] == D2["text"] for p in x["pending"])]
    assert d2, "the D' bundle was published"
    assert [p["text"] for p in d2[0]["pending"]] == [D2["text"]]
    # Every bundle after A' carries only the in-progress draft.
    for x in f[4:]:
        assert _ids(x["pending"]) == [D2["segment_id"]]


async def test_two_unnamed_tracks_share_slot_and_do_not_cover_each_other(env):
    store, bus, _ = env
    A, B, C, _, A2, _ = _wire(speaker="", key="t1")
    X = _seg("t2:1000", 1.0, 4.0, "other track", speaker="", key="t2")
    for s in (A, X, B, C):
        await ingest(store, bus, _msg([s]))
    f = _frames(bus)
    assert _ids(f[-1]["pending"]) == [A["segment_id"], X["segment_id"], B["segment_id"], C["segment_id"]]
    await ingest(store, bus, _msg([A2]))
    last = _frames(bus)[-1]
    assert last["speaker"] == "" and _ids(last["pending"]) == [X["segment_id"]]


async def test_retract_frames_tc_order_and_hash(env):
    store, bus, client = env
    segs = _wire()
    A, B, C, D, A2, _ = segs
    for s in (A, B, C, D, A2):
        await ingest(store, bus, _msg([s]))
    for sid in (B["segment_id"], C["segment_id"]):
        n0 = len(bus.published)
        tc0 = len(await _tc(client))
        await ingest(store, bus, _retract([sid]))
        f = _frames(bus, n0)
        assert [x["type"] for x in f] == ["transcript_retract", "transcript"]
        assert f[0]["segment_ids"] == [sid]
        new_tc = (await _tc(client))[tc0:]
        assert new_tc == [{"type": "retract", "segment_ids": [sid]}]
        assert not await client.hexists(segments_hash_key(MID), sid)
    tc = await _tc(client)
    assert [e.get("type") for e in tc[5:]] == ["retract", "retract"]
    assert [e["segments"][0]["segment_id"] for e in tc[:5]] == _ids([A, B, C, D, A2])
    assert [e["segments"][0]["text"] for e in tc[:5]] == [A["text"], B["text"], C["text"], D["text"], A2["text"]]


async def test_retract_of_confirmed_or_unknown_id_publishes_no_bundle(env):
    store, bus, client = env
    A, *_ = _wire()
    A_conf = dict(A, completed=True)
    await ingest(store, bus, _msg([A_conf]))
    for sid in (A["segment_id"], "nope:1"):
        n0 = len(bus.published)
        await ingest(store, bus, _retract([sid]))
        f = _frames(bus, n0)
        assert f == [{"type": "transcript_retract", "meeting": {"id": MID}, "segment_ids": [sid]}]
        assert (await _tc(client))[-1] == {"type": "retract", "segment_ids": [sid]}


def _today_bundle(segs):
    return {"confirmed": [s for s in segs if s["completed"]], "pending": [s for s in segs if not s["completed"]]}


async def test_teams_csrc_frames_equal_today(env):
    store, bus, client = env
    t1 = _seg("csrc:1:0", 0.0, 2.0, "hello", key="csrc:1", source="merged")
    t2 = _seg("csrc:1:2000", 2.0, 4.0, "there", key="csrc:1", source="merged")
    await ingest(store, bus, _msg([t1]))
    await ingest(store, bus, _msg([t2]))
    f = _frames(bus)
    assert _ids(f[0]["pending"]) == [t1["segment_id"]]
    assert _ids(f[1]["pending"]) == [t2["segment_id"]]  # entry-only, as today
    n0 = len(bus.published)
    await ingest(store, bus, _retract([t1["segment_id"]]))
    assert _frames(bus, n0) == [{"type": "transcript_retract", "meeting": {"id": MID},
                                 "segment_ids": [t1["segment_id"]]}]


class _RaisingStore(InMemoryTranscriptStore):
    async def live_rows(self, meeting_id):
        raise RuntimeError("redis blip")


async def test_live_rows_raising_falls_back_to_today():
    client = fakeredis.aioredis.FakeRedis()
    try:
        store = _RaisingStore(redis_client=client)
        bus = FakeRedisBus(client)
        A, B, C, D, A2, _ = _wire()
        counts = []
        for s in (A, B, C, D, A2):
            counts.append(await ingest(store, bus, _msg([s])))
        assert counts == [1, 1, 1, 1, 1]
        await ingest(store, bus, _retract([B["segment_id"]]))
        f = _frames(bus)
        for frame, s in zip(f[:5], (A, B, C, D, A2)):
            exp = _today_bundle([s])
            assert _ids(frame["pending"]) == _ids(exp["pending"])
            assert _ids(frame["confirmed"]) == _ids(exp["confirmed"])
        assert f[5:] == [{"type": "transcript_retract", "meeting": {"id": MID}, "segment_ids": [B["segment_id"]]}]
        assert await client.hexists(segments_hash_key(MID), A["segment_id"])  # persisted
    finally:
        await client.aclose()
