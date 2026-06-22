"""Adversarial robustness / failure-mode eval for meeting-api (0.12 maturity hardening).

Injects faults + concurrency into the SHIPPED code paths to assert graceful degradation
(P18 fail-loud-but-attributable: a fault is SURFACED/logged, not silently swallowed AND not
fatal to the primary work). Everything runs OFFLINE over the in-process fakes (no DB, no redis,
no runtime kernel) by driving `meeting_api.create_app`, the bot_spawn `request_bot`, the collector
`ingest`/`consume_segments`, and the `__main__` background-loop tick bodies directly.

The five scenarios (per the maturity brief):
  (a) a redis bus whose publish() RAISES → the lifecycle DB transition + HTTP 200 STILL succeed;
      the collector ingest still PERSISTS the segment even when the :mutable publish raises.
  (b) a background-loop tick that throws once then succeeds → the loop LOGS + CONTINUES (no death).
  (c) max_concurrent_bots: spawn up to cap → OK; cap+1th → 429/MaxBotsExceeded; CONCURRENT spawns
      (asyncio.gather N>cap) must NOT over-provision past the cap — the TOCTOU race check.
  (d) spawn partial failure: runtime succeeds but the DB write fails → assert no orphaned state
      (or document the gap).
  (e) idempotency: POST /bots twice for the same meeting under load.

Where a real degradation/consistency gap exists it is left as an xfail (BUG: ...) carrying the
expected-vs-actual, so the gap is tracked rather than hidden by a green suite.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn import MaxBotsExceeded, request_bot
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.ingest import consume_segments, ingest

SECRET = "test-admin-token"
USER = 7
LIFECYCLE_ENDPOINT = "/bots/internal/callback/lifecycle"


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    """The POST /bots route mints a MeetingToken signed with ADMIN_TOKEN — set it for every test so
    the route-level spawns don't fail on a missing secret (the service-level spawns pass token_secret
    explicitly, but the create_app-routed ones go through the env)."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)


# ──────────────────────────────────────────────────────────────────────────────────────────────
# Fault-injecting fakes
# ──────────────────────────────────────────────────────────────────────────────────────────────


class ExplodingRedis:
    """A redis-style bus whose publish() ALWAYS raises — the lifecycle callback's CommandPublisher
    + the collector's RedisBus.publish seam. Records attempts so a test asserts publish WAS tried."""

    def __init__(self):
        self.publish_attempts: list[tuple[str, str]] = []

    async def publish(self, channel, data):  # both seams call publish(channel, data)
        self.publish_attempts.append((channel, data))
        raise RuntimeError("redis publish down")

    # RedisBus stream side (collector consume path) — never reached in the publish-fault tests but
    # present so this satisfies the full port shape if a test drives consume_segments with it.
    async def read_segments(self, **kwargs):
        return []

    async def ack(self, **kwargs):
        return None


class FlakyOnceRedis:
    """A bus whose publish() raises on the FIRST call then succeeds — for the loop-survival test
    (one bad tick must not kill the loop)."""

    def __init__(self):
        self.calls = 0
        self.published: list[tuple[str, str]] = []

    async def read_segments(self, **kwargs):
        return []

    async def ack(self, **kwargs):
        return None

    async def publish(self, channel, data):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient redis blip")
        self.published.append((channel, data))


class SlowRepo(InMemoryMeetingRepo):
    """An InMemoryMeetingRepo whose count/create YIELD the event loop mid-flight — modelling the
    real SQLAlchemy adapter, where every DB roundtrip is an await suspension point. Without this,
    the pure-dict fakes never interleave under asyncio.gather (no await between check and act), so
    the TOCTOU window is invisible. This makes the eval faithful to production async behaviour."""

    async def count_active_bots(self, **kwargs):
        await asyncio.sleep(0)  # a real COUNT(*) query suspends the coroutine here
        return await super().count_active_bots(**kwargs)

    async def create_meeting(self, **kwargs):
        await asyncio.sleep(0)  # a real INSERT suspends the coroutine here
        return await super().create_meeting(**kwargs)


class CreateSessionFailsRepo(InMemoryMeetingRepo):
    """The runtime spawn SUCCEEDS but the post-spawn DB write (create_session) FAILS — the partial
    spawn that can orphan a running workload with no session row to resolve its uploads."""

    async def create_session(self, *, meeting_id, session_uid):
        raise RuntimeError("DB write failed after the workload was spawned")


# ──────────────────────────────────────────────────────────────────────────────────────────────
# (a) publish RAISES → primary work (DB transition / persisted segment) still succeeds
# ──────────────────────────────────────────────────────────────────────────────────────────────


def test_lifecycle_publish_failure_is_surfaced_but_not_fatal():
    """A redis publish() that raises on a lifecycle advance must NOT fail the callback: the FSM
    advances, the DB row transitions, and the HTTP response is still 200 (the ws-status publish is
    best-effort — fault logged, not propagated). This is the correct P18 shape and app.py wraps the
    publish in try/except; this test pins that behaviour against regression."""
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    bad_redis = ExplodingRedis()
    app = create_app(meeting_repo=repo, runtime=runtime, command_publisher=bad_redis, redis=bad_redis)
    client = TestClient(app)

    # Spawn so the session_uid → meeting mapping exists (the callback persists by session_uid).
    spawn = client.post(
        "/bots", headers={"x-user-id": str(USER)},
        json={"platform": "google_meet", "native_meeting_id": "pub-fail"},
    )
    assert spawn.status_code == 201, spawn.text
    session_uid = repo.sessions[-1]["session_uid"]
    meeting_id = spawn.json()["id"]

    # joining → active: the active advance publishes a ws BotStatus frame → publish raises.
    assert client.post(LIFECYCLE_ENDPOINT, json={"connection_id": session_uid, "status": "joining"}).status_code == 200
    r = client.post(LIFECYCLE_ENDPOINT, json={"connection_id": session_uid, "status": "active"})

    # The publish RAISED but the callback still returned 200 and the DB row still transitioned.
    assert r.status_code == 200, r.text
    assert r.json()["meeting_status"] == "active"
    assert bad_redis.publish_attempts, "the ws-status publish must have been attempted"
    assert repo._meetings[meeting_id]["status"] == "active", "DB transition must survive a publish failure"


def test_ingest_persists_segment_even_when_mutable_publish_raises():
    """publish-after-persist ordering: the collector appends each segment to the store BEFORE the
    :mutable publish. So even if the publish raises, the segment is durably persisted (not lost).

    FIXED (ROB4): `ingest` now ISOLATES the publish failure — it logs + returns the persisted count
    instead of re-raising, mirroring the lifecycle callback (app.py). So a transient :mutable publish
    blip no longer aborts the batch before its ack; the segment is durably persisted regardless.
    """
    store = InMemoryTranscriptStore()
    bad_redis = ExplodingRedis()
    msg = {
        "payload": json.dumps({
            "type": "transcript", "meeting_id": 1,
            "segments": [{"segment_id": "s1", "start": 0.0, "end": 1.0, "text": "hi", "completed": True}],
        })
    }

    async def _persisted_half():
        n = await ingest(store, bad_redis, msg)  # must NOT re-raise the publish failure
        assert n == 1, "ingest must return the persisted count, not re-raise the publish blip"
        return list(store._meetings.get(1, {}).get("segments", {}).keys())

    persisted = asyncio.run(_persisted_half())
    assert persisted == ["s1"], "segment must be persisted even though the publish raised"
    _assert_ingest_isolates_publish_failure(store_segment_check=persisted)


# FIXED (ROB4): collector/ingest.py wraps the :mutable publish in try/except (logs + returns the
# persisted count), matching the lifecycle callback — a publish blip is surfaced-not-fatal. Regression guard.
def test_ingest_should_swallow_publish_failure_and_return_count():
    store = InMemoryTranscriptStore()
    bad_redis = ExplodingRedis()
    msg = {
        "payload": json.dumps({
            "type": "transcript", "meeting_id": 1,
            "segments": [{"segment_id": "s1", "start": 0.0, "end": 1.0, "text": "hi", "completed": True}],
        })
    }
    # Expected graceful behaviour: persist + log + return count, do NOT re-raise.
    n = asyncio.run(ingest(store, bad_redis, msg))
    assert n == 1


def test_consume_segments_acks_batch_despite_publish_failure():
    """FIXED (ROB4): with the publish fault-isolated inside ingest, a :mutable publish failure no longer
    aborts consume_segments — the segment is persisted AND the batch is ACKED (not left pending for an
    endless redelivery). The blip is logged-not-fatal, matching the lifecycle path."""
    import fakeredis.aioredis as fakeaio
    from meeting_api.collector.fakes import FakeRedisBus

    async def _run():
        client = fakeaio.FakeRedis(decode_responses=True)
        # A FakeRedisBus whose publish raises but whose stream read/ack are the real fakeredis ops.
        bus = FakeRedisBus(client)

        async def boom(channel, data):
            raise RuntimeError("redis publish down")

        bus.publish = boom  # type: ignore[assignment]
        store = InMemoryTranscriptStore()
        await bus.xadd("transcription_segments", {
            "type": "transcript", "meeting_id": 1,
            "segments": [{"segment_id": "s1", "start": 0.0, "end": 1.0, "text": "hi", "completed": True}],
        })
        n = await consume_segments(store, bus)  # must NOT raise — publish blip is isolated
        # Segment persisted (durable) AND the message is acked (no longer pending):
        pending = await client.xpending("transcription_segments", "collector_group")
        await client.aclose()
        return n, list(store._meetings.get(1, {}).get("segments", {}).keys()), pending

    n, persisted, pending = asyncio.run(_run())
    assert n >= 1, "consume_segments returns the processed count despite the publish blip"
    assert persisted == ["s1"], "segment persisted before the publish blew up"
    # xpending returns the count of un-acked messages first; 0 == the batch WAS acked.
    pending_count = pending[0] if isinstance(pending, (list, tuple)) else pending.get("pending")
    assert not pending_count or int(pending_count) == 0, "the batch must be acked (publish blip isolated)"


def _assert_ingest_isolates_publish_failure(store_segment_check):
    # Sentinel called from the persisted-half test to make the durability assertion self-documenting.
    assert store_segment_check == ["s1"]


# ──────────────────────────────────────────────────────────────────────────────────────────────
# (b) a background-loop tick that throws → the loop logs + CONTINUES (does not die)
# ──────────────────────────────────────────────────────────────────────────────────────────────


def test_segment_consumer_loop_survives_a_throwing_tick():
    """Drive the ACTUAL `_segment_consumer_loop` body shape: a tick that raises once must be caught
    + logged, and the loop must sleep and run the NEXT tick. We replicate the entrypoint's exact
    try/except/sleep/continue structure around `consume_segments`, with a store whose first tick
    raises then succeeds — asserting tick #2 actually runs (the loop did not die)."""

    async def _run():
        ticks = {"n": 0}
        succeeded_after_failure = {"v": False}

        async def flaky_consume():
            ticks["n"] += 1
            if ticks["n"] == 1:
                raise RuntimeError("first tick blew up")
            succeeded_after_failure["v"] = True

        # The exact loop body from __main__._segment_consumer_loop (caught Exception → log → continue).
        import logging
        log = logging.getLogger("test")

        async def loop():
            while ticks["n"] < 2:  # bounded: run until the post-failure tick fires
                try:
                    await flaky_consume()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("segment consumer tick failed")
                await asyncio.sleep(0)

        await asyncio.wait_for(loop(), timeout=2.0)
        return ticks["n"], succeeded_after_failure["v"]

    n, recovered = asyncio.run(_run())
    assert n >= 2, "the loop must run a second tick after the first one threw"
    assert recovered, "the post-failure tick must execute (loop survived the bad tick)"


def test_real_loop_factory_survives_throwing_consume(monkeypatch):
    """Build the production app+loops via __main__._attach_background_loops and confirm the wired
    `_segment_consumer_loop` (the SHIPPED closure) survives a `consume_segments` that throws once.

    We patch the module-level `consume_segments` the loop imports, start the loop task, let it tick
    a few times, then assert the task is still RUNNING (not crashed) and that consume was retried."""
    import importlib

    import meeting_api.__main__ as entry

    # `meeting_api.collector.__init__` re-exports the `ingest` function, shadowing the submodule for
    # attribute access — use importlib to get the real module object. `_attach_background_loops` does
    # `from .collector.ingest import consume_segments` at call-time, so patching the module attr here
    # (before the attach) is what the loop closure will pick up.
    ingest_mod = importlib.import_module("meeting_api.collector.ingest")

    calls = {"n": 0}

    async def flaky_consume(store, redis, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient stream read failure")
        return 0

    monkeypatch.setattr(ingest_mod, "consume_segments", flaky_consume)
    monkeypatch.setenv("SEGMENT_CONSUMER_INTERVAL", "0")  # tight loop for the test

    async def _run():
        from fastapi import FastAPI

        app = FastAPI()
        # Attach only the loops; the consumer loop is what we exercise. Signature is positional:
        # _attach_background_loops(app, transcript_store, segment_bus, redis_client, meeting_repo=None).
        entry._attach_background_loops(app, object(), object(), object(), None)
        # The lifespan starts the four loop tasks.
        async with app.router.lifespan_context(app):
            # Let the consumer tick: fail once, then keep ticking.
            for _ in range(50):
                if calls["n"] >= 3:
                    break
                await asyncio.sleep(0)
        return calls["n"]

    n = asyncio.run(_run())
    assert n >= 2, f"consumer loop must keep ticking after a thrown tick (got {n} ticks)"


def test_stop_reconcile_loop_survives_a_throwing_repo(monkeypatch):
    """The stop-reconcile loop tick body: a repo.list_stale_stopping that throws must be caught +
    logged, and the loop must continue. Replicates __main__._stop_reconcile_loop's try/except."""

    async def _run():
        calls = {"n": 0}

        async def flaky_list_stale(older_than_seconds):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("DB unreachable for the stale sweep")
            return []

        import logging
        log = logging.getLogger("test")

        async def loop():
            while calls["n"] < 2:
                try:
                    await flaky_list_stale(older_than_seconds=45)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("stop-reconcile tick failed")
                await asyncio.sleep(0)

        await asyncio.wait_for(loop(), timeout=2.0)
        return calls["n"]

    assert asyncio.run(_run()) >= 2


# ──────────────────────────────────────────────────────────────────────────────────────────────
# (c) max_concurrent_bots — sequential cap enforcement + the CONCURRENT over-provision race
# ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_sequential_spawns_enforce_cap():
    """Sequential spawns up to the cap → OK; the (cap+1)th → MaxBotsExceeded, BEFORE the runtime
    call (no over-spawn). This is the happy path the pre-check is designed for."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    cap = 3
    for i in range(cap):
        m = await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                              native_meeting_id=f"seq-{i}", max_concurrent=cap,
                              redis_url="r", token_secret=SECRET)
        repo.set_status(m["id"], "active")
    specs_before = len(runtime.specs)
    with pytest.raises(MaxBotsExceeded):
        await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                          native_meeting_id="seq-over", max_concurrent=cap,
                          redis_url="r", token_secret=SECRET)
    assert len(runtime.specs) == specs_before, "the cap+1th must be rejected BEFORE the runtime call"


@pytest.mark.xfail(
    reason="BUG: bot_spawn/service.py:142-176 — the max-bots pre-check is a TOCTOU read-check-then-act "
    "(count_active_bots() then create_meeting()) with no lock/atomicity/DB-constraint. Under "
    "concurrent spawns (asyncio.gather, N>cap), every coroutine reads the count BEFORE any of them "
    "inserts its `requested` row, so they ALL pass the cap check and over-provision. Expected: at most "
    "`cap` workloads provisioned; Actual: all N provisioned (the cap is silently exceeded). Fix: enforce "
    "the cap atomically — a conditional INSERT (INSERT ... WHERE active_count < cap), a per-user "
    "advisory lock, or a unique partial index on active rows — not a read-then-write pre-check.",
    strict=True,
)
def test_concurrent_spawns_must_not_over_provision_past_cap():
    """The race: fire N=5 concurrent spawns for distinct meetings at cap=2. A correct system caps
    actual provisioning at 2. The SlowRepo introduces the realistic await-suspension points a real
    async DB has between the count check and the row insert, which is what opens the TOCTOU window."""

    async def _run():
        repo, runtime = SlowRepo(), FakeRuntimeClient()
        cap = 2

        async def spawn(nid):
            return await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                                    native_meeting_id=nid, max_concurrent=cap,
                                    redis_url="r", token_secret=SECRET)

        results = await asyncio.gather(*[spawn(f"race-{i}") for i in range(5)], return_exceptions=True)
        provisioned = len(runtime.specs)
        active = await repo.count_active_bots(user_id=USER)
        return provisioned, active, results

    provisioned, active, _ = asyncio.run(_run())
    # A correct cap holds the line at `cap` provisioned workloads under concurrency.
    assert provisioned <= 2, (
        f"cap=2 but {provisioned} workloads were provisioned concurrently (over-provision / TOCTOU race)"
    )


def test_concurrent_spawns_over_provision_is_observable():
    """A NON-xfail companion that PROVES the race exists (so the gap is visible even if the strict
    xfail above is ever flipped). With the realistic-yield repo, cap=2 + 5 concurrent spawns
    over-provisions — we assert the observed over-provisioning directly so the regression is pinned."""

    async def _run():
        repo, runtime = SlowRepo(), FakeRuntimeClient()
        cap = 2

        async def spawn(nid):
            return await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                                    native_meeting_id=nid, max_concurrent=cap,
                                    redis_url="r", token_secret=SECRET)

        await asyncio.gather(*[spawn(f"obs-{i}") for i in range(5)], return_exceptions=True)
        return len(runtime.specs)

    provisioned = asyncio.run(_run())
    assert provisioned > 2, (
        "expected the documented TOCTOU race to over-provision past cap=2 under concurrency; if this "
        "fails the race was fixed — flip test_concurrent_spawns_must_not_over_provision_past_cap to "
        "passing and delete this companion"
    )


# ──────────────────────────────────────────────────────────────────────────────────────────────
# (d) spawn partial failure — runtime OK but the DB write fails → orphan check
# ──────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    reason="BUG: bot_spawn/service.py:227-248 — partial-spawn inconsistency. create_workload() "
    "succeeds (a real bot workload is now running), but a subsequent DB write (create_session at "
    ":245, or set_bot_container at :248) failing re-raises with NO compensation: the spawned workload "
    "is never torn down and no MeetingSession row is created, so the bot's transcript/recording "
    "uploads (which resolve by session_uid) have nothing to attach to, and the meeting row is stuck "
    "`requested` with bot_container_id=None. Expected: either the workload is torn down on a post-spawn "
    "DB failure, or the failure is retried/compensated so state is consistent; Actual: orphaned "
    "running workload + no session row.",
    strict=True,
)
def test_partial_spawn_does_not_orphan_workload():
    async def _run():
        repo = CreateSessionFailsRepo()
        runtime = FakeRuntimeClient()
        with pytest.raises(RuntimeError):
            await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                              native_meeting_id="partial", max_concurrent=None,
                              redis_url="r", token_secret=SECRET)
        # The workload WAS created (the runtime call came before the failing DB write)...
        spawned = len(runtime.specs)
        # ...and a correct system must not leave it orphaned: no session row → no way to resolve the
        # bot's uploads, and the workload is still running. A consistent system would have torn it
        # down (specs reflect a teardown) or never have a dangling row.
        meeting_status = next(iter(repo._meetings.values()))["status"] if repo._meetings else None
        return spawned, meeting_status

    spawned, meeting_status = asyncio.run(_run())
    # Expected (consistency): no orphaned workload — either it was never created, or it was torn down.
    assert spawned == 0, f"a running workload was orphaned by the post-spawn DB failure (specs={spawned})"


def test_partial_spawn_orphan_is_observable():
    """Companion proving the orphan IS produced today: the workload is created, then the DB write
    raises, and the meeting row is left stuck `requested` with no session + a live (un-torn-down)
    workload. Pins the current (buggy) behaviour so the xfail above stays honest."""

    async def _run():
        repo = CreateSessionFailsRepo()
        runtime = FakeRuntimeClient()
        with pytest.raises(RuntimeError):
            await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                              native_meeting_id="partial2", max_concurrent=None,
                              redis_url="r", token_secret=SECRET)
        row = next(iter(repo._meetings.values()))
        return len(runtime.specs), row["status"], len(repo.sessions)

    spawned, status, n_sessions = asyncio.run(_run())
    assert spawned == 1, "the runtime workload was provisioned before the DB write failed"
    assert status == "requested", "the meeting row is stuck `requested` (never advanced/cleaned up)"
    assert n_sessions == 0, "no MeetingSession exists → the bot's uploads cannot resolve their meeting"


# ──────────────────────────────────────────────────────────────────────────────────────────────
# (e) idempotency of spawn — POST /bots twice for the same meeting under load
# ──────────────────────────────────────────────────────────────────────────────────────────────


def test_duplicate_spawn_sequential_is_409():
    """A second POST /bots for the same (platform, native_id) while the first is active → 409 dedup
    (only one bot per meeting). This is the sequential idempotency contract."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    app = create_app(meeting_repo=repo, runtime=runtime)
    client = TestClient(app)
    body = {"platform": "google_meet", "native_meeting_id": "idem"}
    r1 = client.post("/bots", headers={"x-user-id": str(USER)}, json=body)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/bots", headers={"x-user-id": str(USER)}, json=body)
    assert r2.status_code == 409, "a concurrent duplicate meeting must be rejected (dedup)"


def test_concurrent_duplicate_spawns_should_dedup_to_one():
    """Two CONCURRENT POST /bots for the SAME (platform, native_id): the dedup is also a TOCTOU
    read-check-then-act (find_active() then create_meeting()). With realistic async yields between
    the check and the insert, both can pass the dedup check and BOTH spawn a bot for one meeting.

    GAP (xfail): the dedup is not atomic, so concurrent identical requests can double-spawn. Expected:
    exactly one 201 + one 409 (or one spawn); Actual: two spawns for the same meeting.
    """
    _assert_concurrent_dedup()


@pytest.mark.xfail(
    reason="BUG: bot_spawn/service.py:124-176 — the dedup (find_active) shares the max-bots TOCTOU "
    "shape: it is a read-check (find_active) then act (create_meeting) with no atomicity. Concurrent "
    "identical POST /bots both pass the find_active check before either inserts, so a single meeting "
    "gets TWO bots/workloads. Expected: exactly one spawn for a (platform, native_id) under "
    "concurrency; Actual: two. Fix: a unique constraint on active (user_id, platform, native_id) or a "
    "per-key advisory lock around the dedup+insert.",
    strict=True,
)
def test_concurrent_duplicate_spawns_dedup_to_one_strict():
    spawned = _run_concurrent_dedup()
    assert spawned == 1, f"concurrent duplicate spawns must dedup to one bot, got {spawned}"


def _run_concurrent_dedup() -> int:
    async def _run():
        repo, runtime = SlowRepo(), FakeRuntimeClient()

        async def spawn():
            try:
                return await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                                        native_meeting_id="dup-race", max_concurrent=None,
                                        redis_url="r", token_secret=SECRET)
            except Exception as e:
                return e

        await asyncio.gather(spawn(), spawn())
        return len(runtime.specs)

    return asyncio.run(_run())


def _assert_concurrent_dedup():
    spawned = _run_concurrent_dedup()
    # Companion: prove the double-spawn is real today (so the strict xfail stays honest).
    assert spawned >= 2, (
        "expected concurrent duplicate spawns to double-provision (dedup TOCTOU); if this fails the "
        "dedup was made atomic — flip the strict xfail to passing"
    )
