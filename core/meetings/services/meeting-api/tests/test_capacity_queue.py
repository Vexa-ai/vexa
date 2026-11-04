"""The capacity queue (lifecycle/queue.py) — a bot waits for a seat instead of being refused.

Founder, 4 Nov 2026 (page:scalability): when the cluster is full a new bot waits and is never
refused, and the API says so; an ad-hoc bot waits up to 30 minutes; queued bots count against the
account's concurrent-bot cap; a bot that never got a seat ends with a clear reason and is not billed.

Every behaviour below fails on the tree before the change (prod/seq61 ab6de36c): `queued` did not
exist, so there was no transition, no notice, no deadline, no cap count, and the reaper treated a
`queued` row as a bot that had been live.

OFFLINE — the shipped app, lifecycle entry and sweep over the in-memory fakes; the runtime's
placement facts are runtime.v1 ``WorkloadStatus.pending`` dicts.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from collector_contracts import assert_api_conforms
from meeting_api import create_app
from meeting_api.bot_spawn import build_router
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.ports import WorkloadUnknown
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.lifecycle.machine import TransitionSource
from meeting_api.lifecycle.provenance import build_service_provenance
from meeting_api.lifecycle.queue import (
    REASON_CLUSTER_AT_CAPACITY,
    REASON_WILL_JOIN_LATE,
    capacity_queue_sweep,
    status_reason_of,
    waits_for_capacity,
)
from meeting_api.lifecycle.reconcile import reconcile_stale_nonterminal_sweep
from meeting_api.webhooks.delivery import DeliveryResult

T0 = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)
SESSION = "sess-queue"
WORKLOAD = "mtg-1-queue"
USER = 7
SECRET = "test-admin-token"


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _unschedulable(**over) -> dict:
    """A bot Pod the cluster autoscaler cannot add a node for (prod's shape: runtime.v1 pending)."""
    pending = {"reason": "unschedulable", "since": _iso(T0 - timedelta(seconds=20)),
               "nominated": False, "autoscaler": "no_scale_up",
               "detail": "pod didn't trigger scale-up: 1 max node group size reached"}
    pending.update(over)
    return pending


class _Log:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def _rec(self, level, msg, *args):
        self.lines.append((level, msg % args if args else msg))

    def info(self, msg, *a):
        self._rec("info", msg, *a)

    def warning(self, msg, *a):
        self._rec("warning", msg, *a)

    def error(self, msg, *a):
        self._rec("error", msg, *a)

    def exception(self, msg, *a):
        self._rec("exception", msg, *a)


class _Sink:
    """A per-user webhook sink that records what it was handed (the real one POSTs it)."""

    def __init__(self):
        self.sent: list[dict] = []

    async def deliver(self, url, envelope, secret=None, *, events_config=None, label=None):
        self.sent.append(envelope)
        return DeliveryResult("delivered", 200)


def _world(*, pending=None, data=None, state="running", workloads=None):
    """A requested meeting with a session and a workload, the app, its sink and the runtime fake."""
    repo = InMemoryMeetingRepo()
    meeting = asyncio.run(repo.create_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        data={"webhook_url": "https://hook.example/x", "transcription_provider": "vexa",
              **(data or {})},
    ))
    asyncio.run(repo.create_session(meeting_id=meeting["id"], session_uid=SESSION))
    asyncio.run(repo.set_bot_container(meeting_id=meeting["id"], bot_container_id=WORKLOAD))
    info = {"workloadId": WORKLOAD, "profile": "meeting-bot", "state": state, "backend": "k8s"}
    if pending is not None:
        info["pending"] = pending
    runtime = FakeRuntimeClient(workloads={WORKLOAD: info} if workloads is None else workloads)
    sink = _Sink()
    app = create_app(open_callbacks=True, meeting_repo=repo, runtime=runtime, webhook_sink=sink)
    return repo, meeting, runtime, sink, app


def _sweep(app, repo, runtime, *, now, log=None) -> dict:
    """One capacity-queue pass, wired exactly as ``__main__._capacity_queue_loop`` wires it."""
    async def _time_out(body):
        code, _ = await app.state.apply_lifecycle_event(
            body, transition_source=TransitionSource.CAPACITY_QUEUE, force_terminal_on_destroy=True)
        return code

    return asyncio.run(capacity_queue_sweep(
        repo, runtime, enter=app.state.apply_queue_transition, time_out=_time_out,
        log=log or _Log(), now=now, max_wait=1800, scale_up_window=300,
    ))


def _row(repo, meeting) -> dict:
    return repo._meetings[meeting["id"]]


def _status_changes(sink) -> list[dict]:
    return [e for e in sink.sent if e["event_type"] == "meeting.status_change"]


# ── 1. detection: Unschedulable AND the cluster cannot grow — nothing else ────────────────────────

@pytest.mark.parametrize("pending,elapsed,expected,why", [
    (_unschedulable(), 20, True, "the autoscaler said no node group can grow (max size reached)"),
    (_unschedulable(autoscaler="scale_up"), 900, False,
     "a node is being added: the normal scale-up wait (90 s p50, 150 s p90) is not a queue"),
    (_unschedulable(autoscaler=None), 120, False,
     "no autoscaler verdict yet, inside the scale-up window"),
    (_unschedulable(autoscaler=None), 301, True,
     "no autoscaler verdict, Unschedulable past the scale-up window: a cluster that cannot grow"),
    (_unschedulable(nominated=True), 900, False,
     "the scheduler nominated a node: a preemption is freeing a seat for it"),
    ({"reason": "image_pull", "detail": "ImagePullBackOff"}, 900, False, "an image pull is not a queue"),
    ({"reason": "starting"}, 900, False, "ContainerCreating (e.g. its env Secret) is not a queue"),
    (None, 900, False, "a running Pod reports no pending facts"),
])
def test_queued_only_when_unschedulable_and_the_cluster_cannot_grow(pending, elapsed, expected, why):
    if isinstance(pending, dict) and pending.get("reason") == "unschedulable":
        pending = {**pending, "since": _iso(T0)}
    now = T0 + timedelta(seconds=elapsed)
    assert waits_for_capacity(pending, now=now, scale_up_window=300) is expected, why


# ── 2. requested → queued (cluster_at_capacity) → will_join_late, each a status_change ───────────

def test_an_ad_hoc_bot_without_a_seat_goes_queued_then_gets_its_late_notice_at_once():
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())

    counts = _sweep(app, repo, runtime, now=T0)

    row = _row(repo, meeting)
    assert counts == {"queued": 1, "late": 1, "timed_out": 0}
    assert row["status"] == "queued"
    assert status_reason_of(row["status"], row["data"]) == REASON_WILL_JOIN_LATE
    assert row["data"]["queue"]["queued_at"] == _iso(T0)
    trail = [(t["from"], t["to"], t["source"], t.get("reason")) for t in row["data"]["status_transition"]]
    assert trail == [("requested", "queued", "capacity_queue", REASON_CLUSTER_AT_CAPACITY),
                     ("queued", "queued", "capacity_queue", REASON_WILL_JOIN_LATE)]
    changes = _status_changes(sink)
    assert [(c["data"]["status_change"]["old_status"], c["data"]["status_change"]["new_status"],
             c["data"]["status_change"]["status_reason"]) for c in changes] == [
        ("requested", "queued", REASON_CLUSTER_AT_CAPACITY),
        ("queued", "queued", REASON_WILL_JOIN_LATE),
    ]
    assert [c["data"]["meeting"]["status_reason"] for c in changes] == [
        REASON_CLUSTER_AT_CAPACITY, REASON_WILL_JOIN_LATE]
    # Two logical events: a receiver deduping on event_id keeps both.
    assert changes[0]["event_id"] != changes[1]["event_id"]
    assert runtime.deleted == [], "a queued bot's Pod is left waiting"


def test_the_queue_notices_are_sent_once():
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())
    _sweep(app, repo, runtime, now=T0)
    _sweep(app, repo, runtime, now=T0 + timedelta(seconds=15))
    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=10))
    assert len(_status_changes(sink)) == 2


def test_a_booked_bot_queued_ahead_of_its_start_is_late_only_at_scheduled_at():
    start = T0 + timedelta(minutes=10)
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable(),
                                               data={"scheduled_at": _iso(start)})
    _sweep(app, repo, runtime, now=T0)
    row = _row(repo, meeting)
    assert row["status"] == "queued"
    assert status_reason_of("queued", row["data"]) == REASON_CLUSTER_AT_CAPACITY

    _sweep(app, repo, runtime, now=start - timedelta(seconds=1))
    assert status_reason_of("queued", _row(repo, meeting)["data"]) == REASON_CLUSTER_AT_CAPACITY

    _sweep(app, repo, runtime, now=start)
    assert status_reason_of("queued", _row(repo, meeting)["data"]) == REASON_WILL_JOIN_LATE
    assert [c["data"]["status_change"]["status_reason"] for c in _status_changes(sink)] == [
        REASON_CLUSTER_AT_CAPACITY, REASON_WILL_JOIN_LATE]

    # Its wait runs from its start, not from the queue entry.
    _sweep(app, repo, runtime, now=start + timedelta(minutes=29))
    assert _row(repo, meeting)["status"] == "queued"
    _sweep(app, repo, runtime, now=start + timedelta(minutes=30))
    assert _row(repo, meeting)["status"] == "failed"


@pytest.mark.parametrize("pending", [
    _unschedulable(autoscaler="scale_up"),
    {"reason": "image_pull", "detail": "ImagePullBackOff"},
    {"reason": "starting"},
    None,
])
def test_a_normal_start_is_never_queued(pending):
    repo, meeting, runtime, sink, app = _world(pending=pending)
    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=20))
    assert _row(repo, meeting)["status"] == "requested"
    assert _status_changes(sink) == []


# ── 3. queued → joining: the bot's own `joining` ends the queue ───────────────────────────────────

def test_a_seat_frees_and_the_bots_joining_ends_the_queue():
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())
    _sweep(app, repo, runtime, now=T0)
    client = TestClient(app)

    r = client.post("/bots/internal/callback/lifecycle",
                    json={"connection_id": SESSION, "status": "joining", "timestamp": _iso(T0)})

    assert r.status_code == 200, r.text
    row = _row(repo, meeting)
    assert row["status"] == "joining"
    assert status_reason_of(row["status"], row["data"]) is None
    tos = [t["to"] for t in row["data"]["status_transition"]]
    assert tos == ["queued", "queued", "joining"], "the queue's trail survives the FSM's first edge"
    # The sweep never puts a booting bot back.
    _sweep(app, repo, runtime, now=T0 + timedelta(hours=1))
    assert _row(repo, meeting)["status"] == "joining"


def test_the_queue_write_never_lands_over_a_bot_that_already_sent_joining():
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())
    repo.set_status(meeting["id"], "joining")
    moved = asyncio.run(app.state.apply_queue_transition(
        session_uid=SESSION, expected=("requested",), status_reason=REASON_CLUSTER_AT_CAPACITY,
        queue_patch={"queued_at": _iso(T0), "status_reason": REASON_CLUSTER_AT_CAPACITY}))
    assert moved is None
    assert _row(repo, meeting)["status"] == "joining"
    assert _status_changes(sink) == []


# ── 4. queued → failed after 30 min: cluster_at_capacity, Pod deleted first, never billed ──────────

def test_after_30_minutes_without_a_seat_the_bot_fails_cluster_at_capacity():
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())
    _sweep(app, repo, runtime, now=T0)

    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=29, seconds=59))
    assert _row(repo, meeting)["status"] == "queued", "inside the 30-minute wait"
    assert runtime.deleted == []

    counts = _sweep(app, repo, runtime, now=T0 + timedelta(minutes=30))

    row = _row(repo, meeting)
    assert counts["timed_out"] == 1
    assert runtime.deleted == [WORKLOAD], "the waiting Pod is torn down"
    assert row["status"] == "failed"
    data = row["data"]
    assert data["completion_reason"] == "cluster_at_capacity"
    assert data["failure_stage"] == "requested"
    assert "30 min" in data["reason"] and "max node group size reached" in data["reason"]
    assert data["join_evidence"]["reason"] == "cluster_at_capacity"
    assert data["join_evidence"]["attribution"] == "system_fault"
    assert data["status_transition"][-1]["source"] == "capacity_queue"
    # The terminal's webhooks: the status_change and the typed bot.failed.
    types = [e["event_type"] for e in sink.sent]
    assert types[-2:] == ["meeting.status_change", "bot.failed"]


def test_a_queue_timeout_is_never_admitted_so_it_is_not_billed():
    """vexa-platform's rating (meeting-billing-rating.ts:188-202) bills nothing for
    bot_outcome never_admitted (reason bot_never_joined). This fixture pins the producer side."""
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())
    _sweep(app, repo, runtime, now=T0)
    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=30))

    row = _row(repo, meeting)
    frozen = row["data"]["service_provenance"]
    assert frozen["bot_outcome"] == "never_admitted"
    assert frozen["bot_admitted_at"] is None and frozen["bot_departed_at"] is None
    assert build_service_provenance(row)["bot_outcome"] == "never_admitted"
    completed = next(e for e in sink.sent if e["event_type"] == "bot.failed")
    assert completed["data"]["meeting"]["service_provenance"]["bot_outcome"] == "never_admitted"


def test_a_bot_that_got_its_seat_at_the_deadline_is_not_timed_out():
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())
    _sweep(app, repo, runtime, now=T0)
    runtime._workloads[WORKLOAD].pop("pending")          # the Pod scheduled; the bot is booting
    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=45))
    assert _row(repo, meeting)["status"] == "queued"
    assert runtime.deleted == []


def test_an_unconfirmed_teardown_never_fails_the_meeting():
    class _Amnesiac(FakeRuntimeClient):
        async def delete_workload(self, workload_id):
            raise WorkloadUnknown(workload_id)

    repo, meeting, _runtime, sink, app = _world(pending=_unschedulable())
    runtime = _Amnesiac(workloads=_runtime._workloads)
    _sweep(app, repo, runtime, now=T0)
    log = _Log()
    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=31), log=log)
    assert _row(repo, meeting)["status"] == "queued"
    assert any(level == "error" for level, _ in log.lines)


def test_a_stopped_queued_bot_is_left_to_the_stop_path():
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())
    _sweep(app, repo, runtime, now=T0)
    _row(repo, meeting)["data"]["stop_requested"] = True
    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=31))
    assert _row(repo, meeting)["status"] == "queued"
    assert runtime.deleted == []


# ── 5. the reaper does not count the queue wait against a queued bot ──────────────────────────────

class _ListsOneStaleRow:
    """The reconcile listing, reduced to the one row: a `queued` meeting quiet for 20 minutes."""

    def __init__(self, status="queued"):
        self.status = status

    async def list_stale_nonterminal(self, **_):
        return [(1, self.status, SESSION, WORKLOAD, False)]


def test_the_reaper_does_not_kill_a_queued_bot_inside_its_wait():
    runtime = FakeRuntimeClient(workloads={WORKLOAD: {
        "workloadId": WORKLOAD, "state": "running", "pending": _unschedulable()}})
    posted: list[dict] = []

    async def post(body):
        posted.append(body)
        return 200

    asyncio.run(reconcile_stale_nonterminal_sweep(
        _ListsOneStaleRow(), runtime, post, stop_grace=45, active_grace=300, preactive_grace=960,
        log=_Log(), untracked_since={},
    ))
    assert posted == [], f"a queued bot inside its wait was reaped: {posted}"
    assert runtime.deleted == []


def test_the_reaper_still_watches_a_queued_bot_whose_pod_died():
    repo = InMemoryMeetingRepo()
    meeting = asyncio.run(repo.create_meeting(user_id=USER, platform="google_meet",
                                              native_meeting_id="abc", data={}))
    asyncio.run(repo.create_session(meeting_id=meeting["id"], session_uid=SESSION))
    asyncio.run(repo.set_bot_container(meeting_id=meeting["id"], bot_container_id=WORKLOAD))
    repo.set_status(meeting["id"], "queued")
    runtime = FakeRuntimeClient(workloads={WORKLOAD: {"workloadId": WORKLOAD, "state": "stopped",
                                                      "exitCode": 1, "stopReason": "failed"}})
    posted: list[dict] = []

    async def post(body):
        posted.append(body)
        return 200

    asyncio.run(reconcile_stale_nonterminal_sweep(
        repo, runtime, post, stop_grace=45, active_grace=300, preactive_grace=960,
        log=_Log(), untracked_since={},
    ))
    assert len(posted) == 1
    body = posted[0]
    assert body["status"] == "failed", "a queued bot never ran: it fails, it does not complete"
    # lifecycle.v1 has no `queued` stage: the event must validate, so the evidence names `requested`.
    assert body.get("failure_stage") in (None, "requested")
    assert body["join_evidence"]["stage"] == "requested"
    assert body["completion_reason"] == "join_failure"


# ── 6. queued counts against the concurrent-bot cap: 429 at the cap ──────────────────────────────

def test_a_queued_bot_holds_its_seat_in_the_account_cap(monkeypatch):
    from fastapi import FastAPI

    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    client = TestClient(app)
    headers = {"x-user-id": str(USER), "x-user-limits": "1"}
    r1 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-1"})
    assert r1.status_code == 201, r1.text
    repo.set_status(r1.json()["id"], "queued")
    assert asyncio.run(repo.count_active_bots(user_id=USER)) == 1

    r2 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-2"})

    assert r2.status_code == 429, r2.text


# ── 7. the API: GET /bots, /meetings and /bots/status say so ─────────────────────────────────────

def test_get_meetings_and_bots_carry_queued_with_its_status_reason():
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
                       status="queued", meeting_id=41, start_time=None,
                       data={"queue": {"queued_at": _iso(T0), "status_reason": REASON_WILL_JOIN_LATE}})
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="xyz-defg-hij",
                       status="active", meeting_id=42,
                       data={"queue": {"queued_at": _iso(T0), "status_reason": REASON_WILL_JOIN_LATE}})
    client = TestClient(create_app(transcript_store=store, meeting_repo=InMemoryMeetingRepo(),
                                   runtime=FakeRuntimeClient()))
    headers = {"X-User-Id": str(USER)}

    for path in ("/meetings", "/bots"):
        body = client.get(path, headers=headers).json()
        by_id = {m["id"]: m for m in body["meetings"]}
        assert by_id[41]["status"] == "queued"
        assert by_id[41]["status_reason"] == REASON_WILL_JOIN_LATE
        assert by_id[42]["status_reason"] is None, "status_reason is set only while queued"
        for m in body["meetings"]:
            assert_api_conforms("MeetingResponse", {k: v for k, v in m.items() if k != "shared"})
    running = client.get("/bots/status", headers=headers).json()["running"]
    assert {m["id"] for m in running} == {41, 42}


# ── 8. review fixes (#2034, factory review at 2f013fa0) ──────────────────────────────────────────

class _CallsBackOnDelete(FakeRuntimeClient):
    """As the real runtime does: DELETE /workloads posts `destroyed` to /runtime/callback BEFORE it
    answers (kernel.destroy → _emit → the callback queue's first attempt)."""

    def __init__(self, app, repo, workloads):
        super().__init__(workloads=workloads)
        self.app, self.repo = app, repo

    async def delete_workload(self, workload_id):
        from meeting_api.lifecycle.reconcile import synthesize_terminal_for_dead_workload

        await super().delete_workload(workload_id)

        async def drive(body):
            code, _ = await self.app.state.apply_lifecycle_event(
                body, transition_source=TransitionSource.RUNTIME_DESTROY, force_terminal_on_destroy=True)
            return code

        await synthesize_terminal_for_dead_workload(self.repo, workload_id, "destroyed", drive, log=_Log())


def test_the_runtimes_synchronous_destroy_callback_ends_the_run_as_the_queue_timeout():
    repo, meeting, fake, sink, app = _world(pending=_unschedulable())
    runtime = _CallsBackOnDelete(app, repo, fake._workloads)
    _sweep(app, repo, runtime, now=T0)
    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=30))
    data = _row(repo, meeting)["data"]
    assert _row(repo, meeting)["status"] == "failed"
    assert data["completion_reason"] == "cluster_at_capacity", "not a re-spawnable join_failure"
    assert data["queue"]["timing_out"] == _iso(T0 + timedelta(minutes=30))


def test_a_restart_between_the_delete_and_the_write_still_ends_cluster_at_capacity():
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())
    _sweep(app, repo, runtime, now=T0)

    async def crash(body):
        raise RuntimeError("meeting-api restarted")

    asyncio.run(capacity_queue_sweep(repo, runtime, enter=app.state.apply_queue_transition,
                                     time_out=crash, log=_Log(), now=T0 + timedelta(minutes=30),
                                     max_wait=1800, scale_up_window=300))
    assert _row(repo, meeting)["status"] == "queued" and runtime.deleted == [WORKLOAD]
    runtime._workloads[WORKLOAD] = {"workloadId": WORKLOAD, "state": "destroyed"}   # gone, tracked
    posted: list[dict] = []

    async def post(body):
        posted.append(body)
        return 200

    asyncio.run(reconcile_stale_nonterminal_sweep(repo, runtime, post, stop_grace=45, active_grace=300,
                                                  preactive_grace=960, log=_Log(), untracked_since={}))
    assert [b["completion_reason"] for b in posted] == ["cluster_at_capacity"]


def test_after_a_restart_the_queue_sweep_finishes_a_timeout_it_began():
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())
    _sweep(app, repo, runtime, now=T0)

    async def crash(body):
        raise RuntimeError("meeting-api restarted")

    asyncio.run(capacity_queue_sweep(repo, runtime, enter=app.state.apply_queue_transition,
                                     time_out=crash, log=_Log(), now=T0 + timedelta(minutes=30),
                                     max_wait=1800, scale_up_window=300))
    runtime._workloads[WORKLOAD] = {"workloadId": WORKLOAD, "state": "destroyed"}   # gone, tracked
    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=30, seconds=15))
    row = _row(repo, meeting)
    assert row["status"] == "failed" and row["data"]["completion_reason"] == "cluster_at_capacity"


@pytest.mark.parametrize("pending", [_unschedulable(nominated=True), _unschedulable(autoscaler="scale_up")])
def test_at_the_deadline_a_pod_whose_seat_is_coming_is_not_failed(pending):
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())
    _sweep(app, repo, runtime, now=T0)
    runtime._workloads[WORKLOAD]["pending"] = pending
    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=31))
    assert _row(repo, meeting)["status"] == "queued" and runtime.deleted == []


def test_the_accounts_queue_wait_stamped_at_spawn_is_the_one_applied():
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable(), data={"queue_wait_s": 600})
    _sweep(app, repo, runtime, now=T0)
    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=10))
    assert _row(repo, meeting)["status"] == "failed"
