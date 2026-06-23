"""STRESS / SHAKE — the control plane under HEAVY concurrency + load.

Distinct from the targeted race tests in test_robustness_seam: these crank the SCALE (N≫cap, chunk
floods, hundreds of stale meetings) and assert the INVARIANTS hold under the yield-modelling fakes that
mimic real-DB await suspension — the cap is NEVER exceeded, no recording update is lost, every stale
workload is torn down, and nothing deadlocks or raises.

Boundary (learning #49): the fakes faithfully model what a SINGLE atomic primitive guarantees (the
per-user cap check; the row-locked recording mutate). True multi-process dedup/cap correctness is the
Postgres advisory-lock + unique-index — proven at L4 (deploy/compose stress on a live stack), noted below.
"""
from __future__ import annotations

import asyncio

import pytest

from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.ports import MaxBotsExceeded
from meeting_api.bot_spawn.service import request_bot
from meeting_api.lifecycle.reconcile import reconcile_stale_stopping_sweep
from meeting_api.recordings import upload_chunk
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage

USER = 7
SECRET = "test-admin-token"


class SlowRepo(InMemoryMeetingRepo):
    """Yields the loop at the count/create roundtrips a real async DB suspends on, so asyncio.gather
    genuinely interleaves (exposes the TOCTOU window the pure-dict fake would hide)."""

    async def count_active_bots(self, **kwargs):
        await asyncio.sleep(0)
        return await super().count_active_bots(**kwargs)

    async def create_meeting(self, **kwargs):
        await asyncio.sleep(0)
        return await super().create_meeting(**kwargs)


class _YieldingStorage(InMemoryStorage):
    async def upload(self, key, data, *, content_type):
        await asyncio.sleep(0)
        await super().upload(key, data, content_type=content_type)


def _wav(n: int = 4) -> bytes:
    import struct

    data = b"\x00" * n
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
    chunk = struct.pack("<4sI", b"data", len(data)) + data
    return struct.pack("<4sI4s", b"RIFF", 4 + len(fmt) + len(chunk), b"WAVE") + fmt + chunk


# ── (1) spawn cap holds under heavy concurrency ──────────────────────────────────────────────────


async def test_stress_spawn_cap_holds_at_scale():
    """cap=5, 50 concurrent DISTINCT spawns through the yield-modelling repo → EXACTLY 5 workloads
    provisioned; the other 45 are capped. The invariant: provisioning never exceeds the cap, at scale."""
    repo, runtime = SlowRepo(), FakeRuntimeClient()
    cap = 5

    async def spawn(i):
        try:
            await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                              native_meeting_id=f"stress-{i}", max_concurrent=cap,
                              redis_url="r", token_secret=SECRET)
            return "ok"
        except MaxBotsExceeded:
            return "capped"

    results = await asyncio.gather(*[spawn(i) for i in range(50)])
    provisioned = len(runtime.specs)
    assert provisioned == cap, f"cap={cap} but {provisioned} workloads provisioned under 50 concurrent spawns"
    assert results.count("ok") == cap
    assert results.count("capped") == 50 - cap


# ── (2) recording chunk flood — no lost update at scale ──────────────────────────────────────────


async def test_stress_recording_chunk_flood_no_lost_update():
    """One recording, then 25 chunks uploaded CONCURRENTLY (interleaving at the storage await). The
    atomic mutate_recordings must fold every one — chunk_count == 26, no lost update under the flood."""
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=1, user_id=USER, session_uid="sess")
    storage = _YieldingStorage()

    # establish the recording with chunk 0
    await upload_chunk(repo, storage, token_meeting_id=1, session_uid="sess",
                       data=_wav(), media_format="wav", chunk_seq=0, is_final=False)
    # flood 25 more concurrently
    await asyncio.gather(*[
        upload_chunk(repo, storage, token_meeting_id=1, session_uid="sess",
                     data=_wav(), media_format="wav", chunk_seq=i, is_final=False)
        for i in range(1, 26)
    ])

    recs = await repo.get_recordings(1)
    bot = [r for r in recs if r.get("source") == "bot"]
    assert len(bot) == 1, f"one recording for the session, got {len(bot)}"
    mf = next(m for m in bot[0]["media_files"] if m["type"] == "audio")
    assert mf["chunk_count"] == 26, f"every chunk must fold (no lost update), got {mf['chunk_count']}"


# ── (3) reconcile sweep at scale — every stale workload torn down ─────────────────────────────────


class _ManyStaleRepo:
    def __init__(self, n):
        self._stale = [(i, f"sess-{i}", f"mtg-{i}-wl") for i in range(n)]

    async def list_stale_stopping(self, *, older_than_seconds):
        return list(self._stale)


async def test_stress_reconcile_kills_all_stale_workloads():
    """200 meetings stuck `stopping` → ONE sweep completes every row AND tears down every workload
    (CC6 at scale): no orphan survives a backlog."""
    import logging

    n = 200
    repo = _ManyStaleRepo(n)
    runtime = FakeRuntimeClient()
    posted = []

    async def post_lifecycle(body):
        posted.append(body)
        return 200

    count = await reconcile_stale_stopping_sweep(
        repo, runtime, post_lifecycle, stop_grace=45, log=logging.getLogger("stress"),
    )
    assert count == n
    assert len(posted) == n, "every stale row must be completed via the lifecycle callback"
    assert len(runtime.deleted) == n, "every orphan workload must be torn down"
    assert set(runtime.deleted) == {f"mtg-{i}-wl" for i in range(n)}
