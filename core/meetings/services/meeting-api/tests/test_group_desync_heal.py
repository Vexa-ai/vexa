"""WP-M9 workstream B, incident (2) — the consumer group's trim+recreate cursor desync.

Live incident: the ``transcription_segments`` stream was trimmed and recreated under the
surviving ``collector_group``, leaving the group's bookkeeping AHEAD of the stream
(entries-read > entries-added). XREADGROUP '>' then saw NOTHING while XLEN > 0 — and with
lag 0 and pending 0 the group looked perfectly healthy while ingestion silently stopped.
A manual ``XGROUP SETID <stream> <group> 0`` was the only recovery.

Contract under test (``collector.ingest.GroupDesyncHealer``):
  * the wedge shape — XLEN > 0, lag <= 0, pending 0, AND the impossible counter relation
    entries_read > entries_added — persisting 2 CONSECUTIVE probes triggers one loud
    ``log.warning`` + a group-cursor reset to 0 (heals within two ticks);
  * one clean probe in between re-arms the confirmation (a transient shape never trips it);
  * the NORMAL quiet state between db-writer trims (consumed-but-not-yet-trimmed entries:
    XLEN > 0, lag 0, pending 0, counters EQUAL) never heals — matching the loose shape alone
    would replay the whole stream every couple of ticks forever;
  * the replayed entries land through the same ingest path without duplicates (the durable
    sink upserts by segment_id).

Driven against ``fakeredis.aioredis`` (XINFO STREAM/GROUPS with the Redis-7 counters, and
XGROUP SETID ENTRIESREAD to manufacture the incident's bookkeeping) — no real redis. The
confirmation logic is additionally driven through a scripted ``RedisBus`` stub, the
injectable seam the healer is designed around.
"""
from __future__ import annotations

import logging

import pytest

fakeredis = pytest.importorskip("fakeredis")
from fakeredis import aioredis as fake_aioredis  # noqa: E402

from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore  # noqa: E402
from meeting_api.collector.ingest import (  # noqa: E402
    CONSUMER_GROUP,
    STREAM_NAME,
    GroupDesyncHealer,
    consume_segments,
)

_LOGGER = "meeting_api.collector.ingest"


async def _seeded_group(n: int = 3):
    """A stream with ``n`` one-segment messages and the collector group created at 0."""
    client = fake_aioredis.FakeRedis(decode_responses=True)
    bus = FakeRedisBus(client)
    store = InMemoryTranscriptStore()
    seg_ids = []
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
    await client.xgroup_create(name=STREAM_NAME, groupname=CONSUMER_GROUP, id="0", mkstream=True)
    return client, bus, store, seg_ids


async def _wedge(client) -> None:
    """Manufacture the incident's bookkeeping: a cursor + read counter that OUTLIVED the stream.
    ENTRIESREAD far beyond entries-added parks last-delivered at the stream head with nothing
    pending — exactly the post-trim+recreate state where '>' reads starve."""
    await client.xgroup_setid(STREAM_NAME, CONSUMER_GROUP, "0-0", entries_read=1000)


def _heal_warnings(caplog):
    return [
        r for r in caplog.records
        if r.name == _LOGGER and r.levelno == logging.WARNING and "desynced" in r.getMessage()
    ]


async def test_the_wedge_starves_consumption_while_entries_exist():
    """The RED shape on head code: '>' sees nothing, XLEN > 0, and the group looks healthy
    (lag <= 0, pending 0) — only the counters betray it."""
    client, bus, store, _ = await _seeded_group(3)
    await _wedge(client)

    assert await consume_segments(store, bus) == 0, "the starved read persists nothing"
    assert await client.xlen(STREAM_NAME) == 3

    probe = await bus.group_backlog(group=CONSUMER_GROUP, stream=STREAM_NAME)
    assert probe["length"] == 3 and probe["pending"] == 0 and probe["lag"] <= 0
    assert probe["entries_read"] > probe["entries_added"]
    await client.aclose()


async def test_healer_confirms_two_ticks_then_resets_and_replay_lands_without_duplicates(caplog):
    """The regression contract: the desync heals within two ticks, the reset is loud, and the
    replayed entries land through the normal ingest path exactly once (upsert by segment_id) —
    including entries that were ALREADY persisted before the wedge."""
    client, bus, store, seg_ids = await _seeded_group(3)
    # normal life first: the batch is consumed, acked, and persisted once
    assert await consume_segments(store, bus) == 3
    await _wedge(client)

    healer = GroupDesyncHealer(bus)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert await healer.tick() is False, "first strike only observes"
        assert _heal_warnings(caplog) == []
        assert await healer.tick() is True, "second consecutive strike heals"

    warnings = _heal_warnings(caplog)
    assert len(warnings) == 1, "exactly one loud warning per heal"
    message = warnings[0].getMessage()
    assert CONSUMER_GROUP in message and STREAM_NAME in message

    # the replay drains through the SAME consume path and the upsert keeps segments unique
    assert await consume_segments(store, bus) == 3
    persisted = sorted(store._meetings[1]["segments"].keys())
    assert persisted == seg_ids, f"replay must land every segment exactly once: {persisted}"
    assert await consume_segments(store, bus) == 0, "nothing left after the replay"

    # post-heal the counters are sane again — the healer disarms instead of looping
    assert await healer.tick() is False
    assert await healer.tick() is False
    assert len(_heal_warnings(caplog)) == 1
    await client.aclose()


async def test_the_normal_quiet_state_between_trims_never_trips_the_healer(caplog):
    """Consumed-but-not-yet-trimmed entries show XLEN > 0 with lag 0 and pending 0 — the SAME
    loose shape as the wedge. The counter discriminator (entries_read == entries_added) must
    keep the healer silent, or every quiet meeting would replay its stream every two ticks."""
    client, bus, store, _ = await _seeded_group(3)
    assert await consume_segments(store, bus) == 3  # consumed + acked; stream NOT trimmed

    probe = await bus.group_backlog(group=CONSUMER_GROUP, stream=STREAM_NAME)
    assert probe["length"] == 3 and probe["lag"] == 0 and probe["pending"] == 0, (
        "the loose shape alone matches the healthy state — this is why the counters gate the heal"
    )

    healer = GroupDesyncHealer(bus)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for _ in range(5):
            assert await healer.tick() is False
    assert _heal_warnings(caplog) == []
    await client.aclose()


class _ScriptedBus:
    """A pure ``RedisBus``-shaped stub for the confirmation logic — the injectable seam."""

    def __init__(self, probes):
        self.probes = list(probes)
        self.resets = 0

    async def group_backlog(self, *, group, stream):
        return self.probes.pop(0)

    async def reset_group_cursor(self, *, group, stream):
        self.resets += 1


_WEDGE = {"length": 3, "pending": 0, "lag": 0, "entries_read": 10, "entries_added": 3}
_HEALTHY = {"length": 3, "pending": 0, "lag": 0, "entries_read": 3, "entries_added": 3}


async def test_one_clean_probe_rearms_the_confirmation():
    """wedge → clean → wedge must NOT heal on the third probe: the shape has to persist for two
    CONSECUTIVE checks, so a transient read never resets a live group."""
    bus = _ScriptedBus([_WEDGE, _HEALTHY, _WEDGE, _WEDGE])
    healer = GroupDesyncHealer(bus)

    assert await healer.tick() is False  # strike 1
    assert await healer.tick() is False  # clean — re-arms
    assert await healer.tick() is False  # strike 1 again
    assert bus.resets == 0
    assert await healer.tick() is True   # strike 2 — heals
    assert bus.resets == 1


@pytest.mark.parametrize("probe", [
    None,                                                     # no stream / no group / no XINFO
    {**_WEDGE, "entries_read": None, "entries_added": None},  # pre-7 Redis: counters unreported
    {**_WEDGE, "lag": None},                                  # lag unknown — never a verdict
    {**_WEDGE, "pending": 2},                                 # in-flight batch — a live group
    {**_WEDGE, "length": 0},                                  # nothing to replay
])
async def test_incomplete_or_healthy_evidence_never_heals(probe):
    bus = _ScriptedBus([probe] * 4)
    healer = GroupDesyncHealer(bus)
    for _ in range(4):
        assert await healer.tick() is False
    assert bus.resets == 0
