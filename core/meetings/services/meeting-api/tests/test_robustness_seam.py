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
from meeting_api.bot_spawn import MaxBotsExceeded, SpawnFailed, request_bot
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
# (b2) CC6 — stop-reconcile GUARANTEES teardown: a stale `stopping` meeting whose ACTIVE bot missed
#      the fire-and-forget leave must have its WORKLOAD killed, not just its DB row completed (ADR-0024).
# ──────────────────────────────────────────────────────────────────────────────────────────────


class _StaleStoppingRepo:
    """Minimal repo for the reconcile sweep: returns the given stale `stopping` rows verbatim."""

    def __init__(self, stale):
        self._stale = stale

    async def list_stale_stopping(self, *, older_than_seconds):
        return list(self._stale)


async def test_stop_reconcile_kills_orphan_workload():
    """An ACTIVE bot that missed the graceful leave leaves the meeting stuck `stopping` with a LIVE
    workload. The reconcile sweep must complete the DB row AND kill the workload (CC6 / ADR-0024) —
    a stop GUARANTEES teardown, it does not merely request it over a channel the bot may have missed."""
    import logging

    from meeting_api.lifecycle.reconcile import reconcile_stale_stopping_sweep

    repo = _StaleStoppingRepo([(42, "sess-42", "mtg-42-deadbeef")])
    runtime = FakeRuntimeClient()
    posted: list[dict] = []

    async def post_lifecycle(body):
        posted.append(body)
        return 200

    n = await reconcile_stale_stopping_sweep(
        repo, runtime, post_lifecycle, stop_grace=45, log=logging.getLogger("t"),
    )

    assert n == 1
    assert runtime.deleted == ["mtg-42-deadbeef"], "the orphan workload must be torn down"
    assert posted and posted[0]["status"] == "completed", "the DB row must also be completed via the callback"


async def test_stop_reconcile_no_container_id_does_not_crash():
    """A stale `stopping` meeting whose bot_container_id was never written still completes — the sweep
    skips the kill (nothing to target) and never raises."""
    import logging

    from meeting_api.lifecycle.reconcile import reconcile_stale_stopping_sweep

    repo = _StaleStoppingRepo([(7, "sess-7", None)])
    runtime = FakeRuntimeClient()

    async def post_lifecycle(body):
        return 200

    n = await reconcile_stale_stopping_sweep(
        repo, runtime, post_lifecycle, stop_grace=45, log=logging.getLogger("t"),
    )
    assert n == 1
    assert runtime.deleted == [], "no container id → no kill, no crash"


async def test_stop_reconcile_kill_failure_never_completes_the_row():
    """A runtime.delete_workload that RAISES is caught (logged) and never crashes the sweep — but
    the row is NOT completed: an UNCONFIRMED teardown must never produce a terminal meeting over a
    possibly-live container (the orphaned-live-bot incident). The row stays `stopping` and the next
    tick retries; other rows in the same sweep are still processed."""
    import logging

    from meeting_api.lifecycle.reconcile import reconcile_stale_stopping_sweep

    class _ThrowingRuntime(FakeRuntimeClient):
        async def delete_workload(self, workload_id):
            if workload_id == "mtg-9-x":
                raise RuntimeError("kernel unreachable")
            await super().delete_workload(workload_id)

    repo = _StaleStoppingRepo([(9, "sess-9", "mtg-9-x"), (10, "sess-10", "mtg-10-ok")])
    posted: list[dict] = []

    async def post_lifecycle(body):
        posted.append(body)
        return 200

    n = await reconcile_stale_stopping_sweep(
        repo, _ThrowingRuntime(), post_lifecycle, stop_grace=45, log=logging.getLogger("t"),
    )
    assert n == 1, "only the CONFIRMED teardown completes; the failed one is retried next sweep"
    assert [b["connection_id"] for b in posted] == ["sess-10"], (
        "the unconfirmed row must NOT be completed — no terminal meeting over a possibly-live bot"
    )


async def test_stop_reconcile_runtime_404_never_completes_the_row():
    """THE INCIDENT (defect C): the user pressed Stop, the recreated runtime 404'd the DELETE, and
    the meeting was completed anyway — over a container still capturing audio. A WorkloadUnknown
    (404) teardown is UNCONFIRMED: the row must stay `stopping` (loud in the logs), never
    completed."""
    import logging

    from meeting_api.lifecycle.reconcile import reconcile_stale_stopping_sweep

    repo = _StaleStoppingRepo([(1, "sess-1", "mtg-1-38a5a399")])
    runtime = FakeRuntimeClient(workloads={})   # the kernel knows NOTHING (post-recreate registry)
    posted: list[dict] = []

    async def post_lifecycle(body):
        posted.append(body)
        return 200

    n = await reconcile_stale_stopping_sweep(
        repo, runtime, post_lifecycle, stop_grace=45, log=logging.getLogger("t"),
    )
    assert n == 0
    assert posted == [], "a runtime 404 must never advance the meeting to completed"
    assert runtime.deleted == [], "nothing was confirmed torn down"


async def test_http_runtime_client_delete_404_raises_workload_unknown():
    """The production adapter's contract: DELETE /workloads/{id} → 404 raises WorkloadUnknown
    (termination UNCONFIRMED), any other non-2xx raises SpawnFailed, a 2xx returns cleanly.
    The old adapter swallowed ALL of it — the incident's DELETE → 404 → 'success'."""
    import pytest

    from meeting_api.bot_spawn.adapters import HttpRuntimeClient
    from meeting_api.bot_spawn.ports import SpawnFailed, WorkloadUnknown

    class _Resp:
        def __init__(self, status_code):
            self.status_code = status_code

    class _StubHttp:
        def __init__(self, status_code):
            self._code = status_code
            self.deleted: list[str] = []

        async def delete(self, url, timeout=None):
            self.deleted.append(url)
            return _Resp(self._code)

    rt404 = HttpRuntimeClient(_StubHttp(404), "http://runtime:8090")
    with pytest.raises(WorkloadUnknown):
        await rt404.delete_workload("mtg-2-d93eee39")

    rt500 = HttpRuntimeClient(_StubHttp(500), "http://runtime:8090")
    with pytest.raises(SpawnFailed):
        await rt500.delete_workload("mtg-2-d93eee39")

    ok = _StubHttp(200)
    await HttpRuntimeClient(ok, "http://runtime:8090").delete_workload("mtg-2-d93eee39")
    assert ok.deleted == ["http://runtime:8090/workloads/mtg-2-d93eee39"]


# ──────────────────────────────────────────────────────────────────────────────────────────────
# (b3) CC5 — a workload that DIES before the bot reports drives the meeting to `failed` (no hang).
# ──────────────────────────────────────────────────────────────────────────────────────────────


class _ContainerRepo:
    """Minimal repo for the CC5 decision: maps workload_id → {meeting_id, status, session_uid}."""

    def __init__(self, by_container):
        self._by_container = by_container

    async def find_by_container(self, *, bot_container_id):
        return self._by_container.get(bot_container_id)


def _cc5_log():
    import logging
    return logging.getLogger("cc5-test")


async def test_runtime_callback_drives_failed_for_pre_active_dead_workload():
    """A terminal workload state while the meeting is still PRE-ACTIVE → a synthetic `failed` is driven
    (the bot never started/reported and never will), so the meeting does not hang `joining` forever."""
    from meeting_api.lifecycle.reconcile import synthesize_failed_for_dead_workload

    repo = _ContainerRepo({"mtg-5-x": {"meeting_id": 5, "status": "joining", "session_uid": "sess-5"}})
    driven = []

    async def drive_failed(ev):
        driven.append(ev)
        return 200

    ok = await synthesize_failed_for_dead_workload(repo, "mtg-5-x", "destroyed", drive_failed, log=_cc5_log())
    assert ok is True
    assert len(driven) == 1
    ev = driven[0]
    assert ev["status"] == "failed"
    assert ev["connection_id"] == "sess-5"
    assert ev["failure_stage"] == "joining"          # the stage it died in
    assert ev["completion_reason"] == "join_failure"


async def test_runtime_callback_noop_when_meeting_already_active():
    """An ACTIVE meeting is owned by the bot's own lifecycle callback — a runtime `destroyed` must NOT
    override it (that would race a legitimate completion)."""
    from meeting_api.lifecycle.reconcile import synthesize_failed_for_dead_workload

    repo = _ContainerRepo({"mtg-6-x": {"meeting_id": 6, "status": "active", "session_uid": "sess-6"}})
    driven = []

    async def drive_failed(ev):
        driven.append(ev)
        return 200

    ok = await synthesize_failed_for_dead_workload(repo, "mtg-6-x", "destroyed", drive_failed, log=_cc5_log())
    assert ok is False and driven == []


async def test_runtime_callback_noop_for_non_terminal_state_or_unknown_workload():
    from meeting_api.lifecycle.reconcile import synthesize_failed_for_dead_workload

    repo = _ContainerRepo({"mtg-7-x": {"meeting_id": 7, "status": "joining", "session_uid": "sess-7"}})
    driven = []

    async def drive_failed(ev):
        driven.append(ev)
        return 200

    # non-terminal workload state → no-op
    assert await synthesize_failed_for_dead_workload(repo, "mtg-7-x", "starting", drive_failed, log=_cc5_log()) is False
    # unknown workload → no-op
    assert await synthesize_failed_for_dead_workload(repo, "ghost", "destroyed", drive_failed, log=_cc5_log()) is False
    assert driven == []


async def test_fake_find_by_container_roundtrip():
    """The InMemory fake's find_by_container resolves a spawned meeting by its workload id (so the CC5
    seam test and the SQL adapter agree on the contract)."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    m = await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                          native_meeting_id="cc5-roundtrip", max_concurrent=5,
                          redis_url="r", token_secret=SECRET)
    workload_id = runtime.specs[-1]["workloadId"]
    repo.set_status(m["id"], "joining")
    info = await repo.find_by_container(bot_container_id=workload_id)
    assert info is not None
    assert info["status"] == "joining"
    assert info["session_uid"]


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


def test_concurrent_spawns_must_not_over_provision_past_cap():
    """FIXED (ROB1): the cap is now enforced ATOMICALLY — service.py replaced the read-check-then-act
    (count_active_bots() then create_meeting()) with a single create_meeting_guarded() that does
    dedup+cap+insert in one transaction (the real adapter serializes per-user via pg_advisory_xact_lock
    + a unique partial index; the fake has no await between the check and the insert). The race: fire
    N=5 concurrent spawns for distinct meetings at cap=2. A correct system caps actual provisioning at
    2. The SlowRepo introduces the realistic await-suspension points a real async DB has between the
    count check and the row insert, which is what opened the TOCTOU window the fix closes."""

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


def test_concurrent_spawns_hold_the_cap_under_load():
    """Companion to the ROB1 fix: with the realistic-yield SlowRepo, cap=2 + 5 concurrent spawns must
    NOT over-provision — the atomic create_meeting_guarded() holds the line at exactly the cap. The
    over-cap spawns raise MaxBotsExceeded (the others succeed); we assert both the provisioned count
    and the active count stay at the cap. (Before ROB1 this same scenario over-provisioned past 2.)"""

    async def _run():
        repo, runtime = SlowRepo(), FakeRuntimeClient()
        cap = 2

        async def spawn(nid):
            return await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                                    native_meeting_id=nid, max_concurrent=cap,
                                    redis_url="r", token_secret=SECRET)

        results = await asyncio.gather(*[spawn(f"obs-{i}") for i in range(5)], return_exceptions=True)
        rejected = [r for r in results if isinstance(r, MaxBotsExceeded)]
        active = await repo.count_active_bots(user_id=USER)
        return len(runtime.specs), active, len(rejected)

    provisioned, active, rejected = asyncio.run(_run())
    assert provisioned <= 2, f"cap=2 must hold under concurrency, but {provisioned} were provisioned"
    assert active <= 2, f"cap=2 active bots max, but {active} are active"
    assert rejected >= 3, f"3 of 5 over-cap spawns must be rejected with MaxBotsExceeded, got {rejected}"


# ──────────────────────────────────────────────────────────────────────────────────────────────
# (d) spawn partial failure — runtime OK but the DB write fails → orphan check
# ──────────────────────────────────────────────────────────────────────────────────────────────


def test_partial_spawn_does_not_orphan_workload():
    """FIXED (ROB3): a post-spawn DB write failure no longer orphans the running workload. The runtime
    spawn (create_workload) succeeds, then create_session raises; service.py now wraps steps 6+7, tears
    the just-created workload DOWN (runtime.delete_workload) and re-raises as SpawnFailed. So the route
    maps it to 502 and the kernel is left with NO orphaned bot (which would otherwise keep running with
    no session row to resolve its uploads)."""

    async def _run():
        repo = CreateSessionFailsRepo()
        runtime = FakeRuntimeClient()
        with pytest.raises(SpawnFailed):
            await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                              native_meeting_id="partial", max_concurrent=None,
                              redis_url="r", token_secret=SECRET)
        # The workload WAS created (the runtime call came before the failing DB write), but it must have
        # been TORN DOWN — no orphan left running. And no session row should survive.
        return len(runtime.specs), len(runtime.deleted), len(repo.sessions)

    spawned, torn_down, n_sessions = asyncio.run(_run())
    assert spawned == 1, "the runtime workload was provisioned before the DB write failed"
    assert torn_down == 1, "the orphaned workload must be torn down (runtime.delete_workload) on a post-spawn DB failure"
    assert n_sessions == 0, "no dangling MeetingSession row should survive the failed spawn"


def test_partial_spawn_tears_down_the_exact_workload():
    """Companion to the ROB3 fix: the workload that was torn down is the SAME id that was spawned (so
    the compensation targets the right orphan), and the failure surfaces as SpawnFailed (→ 502)."""

    async def _run():
        repo = CreateSessionFailsRepo()
        runtime = FakeRuntimeClient()
        with pytest.raises(SpawnFailed):
            await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                              native_meeting_id="partial2", max_concurrent=None,
                              redis_url="r", token_secret=SECRET)
        spawned_id = runtime.specs[0]["workloadId"]
        return spawned_id, runtime.deleted

    spawned_id, deleted = asyncio.run(_run())
    assert deleted == [spawned_id], (
        f"the torn-down workload must be the one that was spawned: spawned={spawned_id!r} deleted={deleted!r}"
    )


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
    """FIXED (ROB2): two CONCURRENT POST /bots for the SAME (platform, native_id) now dedup to ONE
    spawn. The dedup was a TOCTOU read-check-then-act (find_active() then create_meeting()); it is now
    folded into the atomic create_meeting_guarded() (in-txn dedup under a per-user advisory lock, plus a
    unique partial index backstop). With realistic async yields between the check and the insert, only
    one coroutine inserts the active row — the other raises DuplicateMeeting and never spawns."""
    _assert_concurrent_dedup()


def test_concurrent_duplicate_spawns_dedup_to_one_strict():
    """FIXED (ROB2): exactly one spawn for a (platform, native_id) under concurrency."""
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
    # Companion to the ROB2 fix: the dedup is now atomic, so concurrent identical requests spawn EXACTLY
    # one bot (the other is rejected with DuplicateMeeting before it can spawn).
    assert spawned == 1, (
        "concurrent duplicate spawns must dedup to one bot now that the dedup+insert is atomic "
        "(create_meeting_guarded); a count > 1 means the TOCTOU race regressed"
    )


# ──────────────────────────────────────────────────────────────────────────────────────────────
# (A4) missing ADMIN_TOKEN → fail-fast at startup (a misconfig refuses to boot, not 500-per-spawn)
# ──────────────────────────────────────────────────────────────────────────────────────────────


def test_startup_requires_admin_token(monkeypatch):
    """FIXED (A4): the production boot (__main__._require_config, called by build_production_app)
    REFUSES to start when ADMIN_TOKEN is unset — a clear RuntimeError naming the missing var — instead
    of booting fine and 500-ing every POST /bots when mint_meeting_token hits the missing secret deep
    in the request path. So a misconfigured deploy fails loud at boot (P18)."""
    import meeting_api.__main__ as entry

    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    with pytest.raises(RuntimeError) as ei:
        entry._require_config()
    msg = str(ei.value)
    assert "ADMIN_TOKEN" in msg, f"the error must name the missing var, got: {msg!r}"

    # And with it set, the config check passes (the happy path the deploy actually runs).
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    entry._require_config()  # must not raise


def test_mint_meeting_token_surfaces_clear_config_error(monkeypatch):
    """The per-request mint also surfaces a CLEAR error (not a cryptic crypto failure) when ADMIN_TOKEN
    is unset — the deep cause the A4 startup gate prevents reaching in production."""
    from meeting_api.bot_spawn.invocation import mint_meeting_token

    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    with pytest.raises(ValueError) as ei:
        mint_meeting_token(1, USER, "google_meet", "x")
    assert "ADMIN_TOKEN" in str(ei.value)


# ──────────────────────────────────────────────────────────────────────────────────────────────
# (f) spawn/stop race — POST then immediate DELETE must NOT orphan the bot (design gap)
# ──────────────────────────────────────────────────────────────────────────────────────────────


def test_stop_of_booting_bot_tears_down_workload_no_orphan():
    """POST then immediate DELETE while the bot is still BOOTING (status 'requested', not yet subscribed
    to bot_commands): the fire-and-forget leave would be LOST, so the stop must DIRECTLY tear the
    workload down — else the bot boots, joins, and orphans. Asserts the workload was deleted."""
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    client = TestClient(create_app(meeting_repo=repo, runtime=runtime))
    spawn = client.post("/bots", headers={"x-user-id": str(USER)},
                        json={"platform": "google_meet", "native_meeting_id": "orphan-race"})
    assert spawn.status_code == 201, spawn.text
    workload_id = spawn.json()["bot_container_id"]
    assert workload_id and workload_id not in runtime.deleted
    r = client.delete("/bots/google_meet/orphan-race", headers={"x-user-id": str(USER)})
    assert r.status_code == 200, r.text
    assert workload_id in runtime.deleted, \
        "a stop of a still-booting bot must tear its workload down (no orphan), not just publish a leave"


def test_spawn_reconciles_a_stop_that_raced_the_boot():
    """A DELETE marks the meeting stopping WHILE the workload is being created (before set_bot_container
    writes the id, so the stop's own teardown can't target it). The spawn must re-check status after
    writing the id and tear the just-created workload down — closing that race window."""
    runtime = FakeRuntimeClient()

    class _StopRacesRepo(InMemoryMeetingRepo):
        async def set_bot_container(self, *, meeting_id, bot_container_id):
            row = await super().set_bot_container(meeting_id=meeting_id, bot_container_id=bot_container_id)
            self._meetings[meeting_id]["status"] = "stopping"  # a concurrent DELETE raced in
            return row

    client = TestClient(create_app(meeting_repo=_StopRacesRepo(), runtime=runtime))
    r = client.post("/bots", headers={"x-user-id": str(USER)},
                    json={"platform": "google_meet", "native_meeting_id": "raced-spawn"})
    assert r.status_code == 201, r.text
    assert runtime.deleted, "spawn must tear down the workload when a stop raced its boot (no orphan)"
