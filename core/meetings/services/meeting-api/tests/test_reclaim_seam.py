"""#636 · C1/A1+A2 — a surviving replica reclaims a crashed replica's orphaned segment batch.

Every meeting-api replica USED to join the collector group under one hard-coded name
(``collector-main``) and there was no ``XAUTOCLAIM`` anywhere, so a replica that crashed AFTER
``XREADGROUP`` delivered a batch but BEFORE ``XACK`` left those entries in its PEL forever — no peer
could distinguish, let alone reclaim, them. #636 gives each replica a pod-derived identity
(``collector-<hostname>`` / ``COLLECTOR_CONSUMER_NAME``) and adds ``reclaim_segments`` — a bounded,
``min_idle_ms``-gated ``XAUTOCLAIM`` that drains the orphan through the SAME ingest→ack path.

Driven against ``fakeredis.aioredis`` (xautoclaim ≥ 2.21) — no real redis. Same lane as
``test_robustness_seam.py`` / ``test_stream_retention.py``.
"""
from __future__ import annotations

import json

import pytest

fakeredis = pytest.importorskip("fakeredis")
from fakeredis import aioredis as fake_aioredis  # noqa: E402

from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore  # noqa: E402
from meeting_api.collector.ingest import (  # noqa: E402
    CONSUMER_GROUP,
    STREAM_NAME,
    reclaim_segments,
)


async def _crashed_consumer_pel(n: int):
    """The ``crashed_consumer_pel`` fixture: consumer ``collector-dead`` XREADGROUPs a batch of ``n``
    one-segment messages and NEVER acks — so ``n`` entries land un-acked in its PEL, exactly the
    state a replica leaves when it dies between delivery and ack. Returns (client, bus, store, ids)."""
    client = fake_aioredis.FakeRedis(decode_responses=True)
    bus = FakeRedisBus(client)
    store = InMemoryTranscriptStore()
    seg_ids: list[str] = []
    for i in range(1, n + 1):
        sid = f"seg-{i}"
        seg_ids.append(sid)
        await bus.xadd(STREAM_NAME, {
            "type": "transcript", "meeting_id": 1,
            "segments": [{
                "segment_id": sid, "start": float(i), "end": float(i) + 1.0,
                "text": f"t{i}", "completed": True,
            }],
        })
    dead = await bus.read_segments(
        group=CONSUMER_GROUP, consumer="collector-dead", stream=STREAM_NAME, count=n
    )
    assert len(dead) == n, "the dead consumer must hold the whole batch in its PEL"
    return client, bus, store, seg_ids


def _pending_count(pending) -> int:
    if isinstance(pending, dict):
        return int(pending.get("pending") or 0)
    return int(pending[0]) if pending else 0


async def test_survivor_reclaims_crashed_batch():
    """A1 (discriminating). N=5 entries un-acked in ``collector-dead``'s PEL. The survivor's plain
    ``XREADGROUP >`` sees NOTHING (the orphan is not lag — it was already delivered). Only the reclaim
    tick picks it up: all 5 segment_ids land in the store and the group PEL drains to 0.

    RED on head code (no ``reclaim_orphans``/``XAUTOCLAIM``, shared consumer name): the survivor's
    ``>`` read returns nothing, the store stays empty, and XPENDING stays 5 forever."""
    client, bus, store, seg_ids = await _crashed_consumer_pel(5)

    # the orphan is NOT undelivered lag: a fresh new-only read by the survivor returns nothing.
    fresh = await bus.read_segments(
        group=CONSUMER_GROUP, consumer="collector-live", stream=STREAM_NAME
    )
    assert fresh == [], "the orphaned batch is delivered-but-un-acked, invisible to a '>' read"

    reclaimed = await reclaim_segments(
        store, bus, consumer="collector-live", min_idle_ms=0
    )

    assert reclaimed == 5, "the survivor reclaims + persists all 5 orphaned segments"
    persisted = sorted(store._meetings.get(1, {}).get("segments", {}).keys())
    assert persisted == seg_ids, f"all segment_ids must land in the store: {persisted}"
    pending = await client.xpending(STREAM_NAME, CONSUMER_GROUP)
    assert _pending_count(pending) == 0, "the group PEL must drain to 0 after reclaim+ack"
    await client.aclose()


async def test_reclaim_respects_min_idle():
    """A2 (min-idle guard). An entry pending only ~0 ms — the shape of a LIVE peer's in-flight batch,
    delivered and about to ack — must NOT be reclaimed when ``min_idle_ms=60000``. The gate is what
    stops a survivor from STEALING a healthy peer's work.

    Negative control (inline): with the gate OFF (``min_idle_ms=0``) the SAME batch IS reclaimed and
    re-drained through the sink — the duplicate processing the guard exists to prevent (call-count on
    the store sink proves it)."""
    client, bus, store, _ = await _crashed_consumer_pel(3)

    calls = {"n": 0}
    orig_append = store.append_segment

    async def _counting_append(meeting_id, segment):
        calls["n"] += 1
        return await orig_append(meeting_id, segment)

    store.append_segment = _counting_append  # type: ignore[assignment]

    # GREEN: the 60s gate leaves a 0ms-idle (live in-flight) batch untouched — no reclaim, no sink call.
    reclaimed = await reclaim_segments(
        store, bus, consumer="collector-live", min_idle_ms=60000
    )
    assert reclaimed == 0, "a live peer's in-flight (0ms-idle) batch must never be reclaimed"
    assert calls["n"] == 0, "the sink must not be touched when the min-idle gate holds"

    # NEGATIVE CONTROL: gate off → the same batch is stolen and reprocessed (the double-process).
    stolen = await reclaim_segments(
        store, bus, consumer="collector-live", min_idle_ms=0
    )
    assert stolen == 3, "with the gate off the batch IS reclaimed (proves the gate suppressed it)"
    assert calls["n"] == 3, "the stolen batch is re-drained through the sink — the duplicate"
    await client.aclose()


async def test_consumer_name_is_pod_derived():
    """The consumer identity is per-replica, not the old shared ``collector-main``. An explicit
    ``COLLECTOR_CONSUMER_NAME`` override wins; otherwise it is ``collector-<hostname>``."""
    import importlib

    # `meeting_api.collector.__init__` re-exports the `ingest` function, shadowing the submodule for
    # attribute access — use importlib to get the real module object (as test_robustness_seam does).
    ingest_mod = importlib.import_module("meeting_api.collector.ingest")

    assert ingest_mod.CONSUMER_NAME != "collector-main"
    assert ingest_mod.CONSUMER_NAME.startswith("collector-")

    import os as _os
    _os.environ["COLLECTOR_CONSUMER_NAME"] = "collector-pod-xyz"
    try:
        reloaded = importlib.reload(ingest_mod)
        assert reloaded.CONSUMER_NAME == "collector-pod-xyz"
    finally:
        del _os.environ["COLLECTOR_CONSUMER_NAME"]
        importlib.reload(ingest_mod)  # restore the default for other tests


# ── #636 witness regression: reclaim degrades (never crashes the consumer) on a Redis without XAUTOCLAIM ──

async def test_reclaim_orphans_degrades_when_xautoclaim_unsupported():
    """On Redis < 6.2 the server has no ``XAUTOCLAIM`` and replies ``unknown command`` — the v0.12.5
    Lite box bundled Redis 6.0.16, so ``reclaim_segments`` threw every segment-consumer tick and
    transcription died. ``reclaim_orphans`` must now degrade to a **no-op** (return ``[]``), leaving
    the normal ``XREADGROUP`` consume path untouched. This offline test uses fakeredis (which DOES
    have XAUTOCLAIM), so it drives the adapter directly with a client that raises like Redis 6.0."""
    from redis.exceptions import ResponseError

    from meeting_api.collector.adapters import RedisStreamBus

    class _Redis60:
        async def xgroup_create(self, **kw):
            return None

        async def xautoclaim(self, **kw):
            raise ResponseError("unknown command 'XAUTOCLAIM', with args beginning with: ...")

    bus = RedisStreamBus(_Redis60())
    out = await bus.reclaim_orphans(
        group=CONSUMER_GROUP, stream=STREAM_NAME, consumer="collector-x", min_idle_ms=60000
    )
    assert out == [], "unsupported XAUTOCLAIM must degrade to no reclaimed entries, not raise"
    assert bus._reclaim_unsupported is True, "the log-once latch must be set"
    # a subsequent call keeps degrading (and, per the latch, does not re-log)
    assert await bus.reclaim_orphans(
        group=CONSUMER_GROUP, stream=STREAM_NAME, consumer="collector-x", min_idle_ms=1
    ) == []


async def test_reclaim_orphans_reraises_unrelated_response_error():
    """Only 'unknown command' / XAUTOCLAIM ResponseErrors degrade — any OTHER ResponseError must
    propagate (we don't want a real Redis fault silently swallowed as 'no orphans')."""
    from redis.exceptions import ResponseError

    from meeting_api.collector.adapters import RedisStreamBus

    class _RedisBadReply:
        async def xgroup_create(self, **kw):
            return None

        async def xautoclaim(self, **kw):
            raise ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")

    bus = RedisStreamBus(_RedisBadReply())
    with pytest.raises(ResponseError):
        await bus.reclaim_orphans(
            group=CONSUMER_GROUP, stream=STREAM_NAME, consumer="c", min_idle_ms=1
        )
    assert bus._reclaim_unsupported is False, "an unrelated error must NOT flip the unsupported latch"
