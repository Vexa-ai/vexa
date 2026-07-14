"""#527 · C2/A2 — bounded stream retention that NEVER ages out an un-consumed entry.

On 2026-04-26 the stream's MAXLEN trim aged out entries before the (hung) consumer read them → 20
meetings' transcripts permanently lost. The retention here trims only entries that are BOTH older
than the window AND already consumed (acked, and behind the group's last-delivered-id). Un-acked
(pending) and undelivered (lag) entries always survive. Driven against fakeredis — no real redis.
"""
from __future__ import annotations

import pytest

fakeredis = pytest.importorskip("fakeredis")
from fakeredis import aioredis as fake_aioredis  # noqa: E402

from meeting_api.collector.db_writer import _trim_segments_stream  # noqa: E402
from meeting_api.collector.ingest import CONSUMER_GROUP, STREAM_NAME  # noqa: E402

# a "now" far in the future so every seeded entry is older than the retention window
NOW_MS = 10_000_000_000_000
RETENTION_S = 3600


async def _seed(r, n):
    ids = []
    for i in range(1, n + 1):
        ids.append(await r.xadd(STREAM_NAME, {"payload": "{}"}, id=f"{1000 + i}-0"))
    await r.xgroup_create(STREAM_NAME, CONSUMER_GROUP, id="0")
    return ids


async def _ids(r):
    return [e[0] for e in await r.xrange(STREAM_NAME)]


async def test_acked_and_aged_entries_are_trimmed():
    r = fake_aioredis.FakeRedis(decode_responses=True)
    ids = await _seed(r, 5)
    # deliver + ack the first 3 (fully consumed); 4-5 remain UNDELIVERED (the lag)
    msgs = await r.xreadgroup(CONSUMER_GROUP, "c1", {STREAM_NAME: ">"}, count=3)
    delivered = [m[0] for m in msgs[0][1]]
    await r.xack(STREAM_NAME, CONSUMER_GROUP, *delivered)

    await _trim_segments_stream(r, RETENTION_S, NOW_MS)

    remaining = await _ids(r)
    # the 3 acked-and-aged entries are gone; the 2 UNDELIVERED entries survive (never aged out)
    assert ids[0] not in remaining and ids[1] not in remaining and ids[2] not in remaining
    assert ids[3] in remaining and ids[4] in remaining


async def test_pending_entry_is_never_trimmed():
    r = fake_aioredis.FakeRedis(decode_responses=True)
    ids = await _seed(r, 4)
    # deliver 2 but ack only the first → id[1] is PENDING (delivered, un-acked)
    msgs = await r.xreadgroup(CONSUMER_GROUP, "c1", {STREAM_NAME: ">"}, count=2)
    delivered = [m[0] for m in msgs[0][1]]
    await r.xack(STREAM_NAME, CONSUMER_GROUP, delivered[0])

    await _trim_segments_stream(r, RETENTION_S, NOW_MS)

    remaining = await _ids(r)
    assert ids[0] not in remaining, "the acked-and-aged entry should be trimmed"
    assert ids[1] in remaining, "a PENDING (delivered-unacked) entry must survive"
    assert ids[2] in remaining and ids[3] in remaining, "UNDELIVERED entries must survive"


async def test_no_group_does_not_blind_trim():
    r = fake_aioredis.FakeRedis(decode_responses=True)
    for i in range(1, 4):
        await r.xadd(STREAM_NAME, {"payload": "{}"}, id=f"{1000 + i}-0")
    # no consumer group created → the trim must NOT drop entries it can't prove are consumed
    await _trim_segments_stream(r, RETENTION_S, NOW_MS)
    assert len(await _ids(r)) == 3
