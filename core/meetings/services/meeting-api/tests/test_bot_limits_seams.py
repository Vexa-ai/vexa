"""Every bot limit lives in meeting-api, behind one enforcement point and one limits model (founder,
4 Nov 2026: «every bot limit lives in Meetings»; «respect the seams at all times»).

* The bot-start rate is checked AND spent inside ``create_meeting_guarded`` (and the continued-run
  reopen), under the per-user advisory lock, counted from the starts already recorded
  (``meeting_sessions``). Every path that starts a bot passes it: ``POST /bots`` from the gateway or
  from the Flows service door, and the calendar auto-join sweep.
* The account's limits come over one seam: admin-api's ``/internal/users/{id}/bot-context``
  ``bot_limits`` (platform_settings plan defaults, overridden by ``users.data``). meeting-api reads no
  identity table; ``limits.py`` is the one parser.
* The queue wait has one source: the resolved limits, stamped on the run at spawn.

OFFLINE: the shipped router and service over the in-memory fakes; the bot-context answer is
injected where ``request_bot`` asks admin-api for it.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from meeting_api.bot_spawn import build_router, request_bot
from meeting_api.bot_spawn import service as spawn_service
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

SECRET = "test-admin-token"
USER = 7
KW = dict(redis_url="r", token_secret=SECRET, meeting_api_url="http://meeting-api:8080")


def _context(monkeypatch, bot_limits=None, *, cap=100, **extra):
    """What admin-api's bot-context answers for every user in this test: bot-limits.v1 (the one
    carrier of the account's limits) with these limits and this concurrency cap."""
    body = dict(extra)
    if bot_limits is not None:
        body["bot_limits"] = {"version": 1, "max_concurrent_bots": cap, **bot_limits}

    async def fetch(user_id):
        return dict(body)

    monkeypatch.setattr(spawn_service, "_fetch_bot_context", fetch)


def _app(repo, runtime):
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    return app


async def _post_many(app, natives, headers):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://meeting-api") as client:
        return await asyncio.gather(*(
            client.post("/bots", headers=headers,
                        json={"platform": "google_meet", "native_meeting_id": n})
            for n in natives
        ))


def _ended(repo, meeting_id, *, ago_s, stopped=True):
    row = repo._meetings[meeting_id]
    row["status"] = "completed"
    row["end_time"] = (datetime.now(timezone.utc) - timedelta(seconds=ago_s)).isoformat()
    if stopped:
        row["data"]["stop_requested"] = True


# ── 1. the start rate: one enforcement point in meeting-api ────────────────────────────────────

def test_parallel_starts_on_one_account_start_only_the_burst(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    _context(monkeypatch, {"bot_start_rate": {"burst": 3, "per_minute": 0.5}})
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()

    answers = asyncio.run(_post_many(_app(repo, runtime), [f"aaa-bbbb-c{i:02d}" for i in range(8)],
                                     {"x-user-id": str(USER)}))

    codes = sorted(r.status_code for r in answers)
    assert codes == [201] * 3 + [429] * 5, [r.text for r in answers]
    refused = next(r for r in answers if r.status_code == 429)
    assert refused.json() == {"detail": "Bot start rate exceeded", "reason": "bot_start_rate"}
    assert refused.headers["retry-after"] == "120"
    assert len(runtime.specs) == 3, "a refused start reaches no cluster"


def test_the_service_door_path_is_limited(monkeypatch):
    """The Flows service door forwards POST /bots with the subject alone (gateway
    service_door.OUTBOUND_HEADERS: no X-User-Limits). It meets the same rate as any start."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    _context(monkeypatch, {"bot_start_rate": {"burst": 1, "per_minute": 0.5}})
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    door = {"x-user-id": str(USER), "content-type": "application/json", "x-trace-id": "t-1"}

    first, second = asyncio.run(_post_many(_app(repo, runtime), ["aaa-bbbb-ccc"], door))[0], \
        asyncio.run(_post_many(_app(repo, runtime), ["ddd-eeee-fff"], door))[0]

    assert first.status_code == 201, first.text
    assert second.status_code == 429 and second.json()["reason"] == "bot_start_rate"


def test_the_built_in_rate_holds_where_identity_sets_none(monkeypatch):
    """A bot-limits.v1 answer with no rate of its own: the built-in default (burst 10, 0.5 a minute)
    applies, never "no limit"."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    _context(monkeypatch, {})
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    answers = asyncio.run(_post_many(_app(repo, runtime), [f"aaa-bbbb-c{i:02d}" for i in range(12)],
                                     {"x-user-id": str(USER)}))
    assert sorted(r.status_code for r in answers) == [201] * 10 + [429] * 2


def test_without_a_readable_answer_from_a_configured_identity_no_bot_starts(monkeypatch):
    """Identity is configured but gives no bot-limits.v1 (unreachable, an older admin-api, a version
    this build does not know): the start is refused 503, never run past a limit nobody read."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("ADMIN_API_URL", "http://admin-api:8001")
    monkeypatch.setenv("INTERNAL_API_SECRET", "s")
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    for answer in ({}, {"bot_limits": {"version": 2, "max_concurrent_bots": 5}},
                   {"bot_limits": {"version": 1}}):
        async def fetch(user_id, answer=answer):
            return dict(answer)

        monkeypatch.setattr(spawn_service, "_fetch_bot_context", fetch)
        r = asyncio.run(_post_many(_app(repo, runtime), ["aaa-bbbb-ccc"], {"x-user-id": str(USER)}))[0]
        assert r.status_code == 503 and r.headers["retry-after"], (answer, r.text)
    assert runtime.specs == [] and repo._meetings == {}


def test_a_refused_start_costs_nothing(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    _context(monkeypatch, {"bot_start_rate": {"burst": 2, "per_minute": 0.5}}, cap=1)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    app = _app(repo, runtime)
    capped = {"x-user-id": str(USER)}

    ok = asyncio.run(_post_many(app, ["aaa-bbbb-ccc"], capped))[0]
    assert ok.status_code == 201
    dup = asyncio.run(_post_many(app, ["aaa-bbbb-ccc"], capped))[0]
    full = asyncio.run(_post_many(app, ["ddd-eeee-fff"], capped))[0]
    assert dup.status_code == 409 and full.status_code == 429 and "reason" not in full.json()

    _ended(repo, ok.json()["id"], ago_s=600, stopped=False)
    second = asyncio.run(_post_many(app, ["ggg-hhhh-iii"], capped))[0]
    assert second.status_code == 201, "the 409 and the cap's 429 spent no start"
    _ended(repo, second.json()["id"], ago_s=600, stopped=False)
    third = asyncio.run(_post_many(app, ["jjj-kkkk-lll"], capped))[0]
    assert third.status_code == 429 and third.json()["reason"] == "bot_start_rate"


def test_a_continued_run_spends_a_start_too(monkeypatch):
    """continue_meeting reopens a terminal row in place; it is a start like any other."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    _context(monkeypatch, {"bot_start_rate": {"burst": 1, "per_minute": 0.5}})
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    m = asyncio.run(request_bot(repo, runtime, user_id=USER, platform="google_meet",
                                native_meeting_id="aaa-bbbb-ccc", **KW))
    _ended(repo, m["id"], ago_s=600, stopped=False)
    from meeting_api.bot_spawn.ports import BotStartRateExceeded

    with pytest.raises(BotStartRateExceeded):
        asyncio.run(request_bot(repo, runtime, user_id=USER, platform="google_meet",
                                native_meeting_id="aaa-bbbb-ccc", continue_meeting=True, **KW))


def test_a_booked_start_spends_the_same_rate(monkeypatch):
    """The auto-join sweep spawns through request_bot, so its starts are counted and limited too."""
    from meeting_api.bot_spawn.auto_join import auto_join_tick

    _context(monkeypatch, {"bot_start_rate": {"burst": 1, "per_minute": 0.5}})
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    now = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
    for native in ("aaa-bbbb-ccc", "ddd-eeee-fff"):
        row = asyncio.run(repo.create_meeting(user_id=USER, platform="google_meet",
                                              native_meeting_id=native,
                                              data={"scheduled_at": "2026-11-04T10:01:00Z",
                                                    "auto_join": True}))
        repo._meetings[row["id"]]["status"] = "scheduled"

    async def ctx(user_id):
        return {"bot_limits": {"version": 1, "max_concurrent_bots": 5,
                               "bot_start_rate": {"burst": 1, "per_minute": 0.5}}}

    counters = asyncio.run(auto_join_tick(repo, runtime, fetch_bot_context=ctx, now=now,
                                          token_secret=SECRET, redis_url="r"))
    assert counters["spawned"] == 1 and counters["errors"] == 1, counters
    stamped = [m["data"].get("auto_join_error") for m in repo._meetings.values()]
    assert any(e and "start rate" in e.lower() for e in stamped), stamped


# ── 2. one limits model, over the bot-context seam ─────────────────────────────────────────────

def test_an_account_override_changes_the_seat_cooldown(monkeypatch):
    """users.data["bot_limits"]["seat_cooldown_s"] = 300, resolved by admin-api into bot-context."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    _context(monkeypatch, {"seat_cooldown_s": 300}, cap=1)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    app = _app(repo, runtime)
    capped = {"x-user-id": str(USER)}
    first = asyncio.run(_post_many(app, ["aaa-bbbb-ccc"], capped))[0]
    _ended(repo, first.json()["id"], ago_s=120)

    again = asyncio.run(_post_many(app, ["ddd-eeee-fff"], capped))[0]

    assert again.status_code == 429, "120 s is inside this account's 300 s cooldown (default 60)"
    assert again.headers["retry-after"] == "300"


def test_the_accounts_queue_wait_is_stamped_on_the_run(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    _context(monkeypatch, {"queue_wait_s": 600})
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    m = asyncio.run(request_bot(repo, runtime, user_id=USER, platform="google_meet",
                                native_meeting_id="aaa-bbbb-ccc", **KW))
    assert repo._meetings[m["id"]]["data"]["queue_wait_s"] == 600


def test_a_booked_start_uses_the_accounts_cooldown_when_booked_for_now(monkeypatch):
    """L3: auto-join's last-minute plan gets the account's resolved cooldown, not the built-in one."""
    _context(monkeypatch, {"seat_cooldown_s": 300}, cap=1)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    m = asyncio.run(request_bot(repo, runtime, user_id=USER, platform="google_meet",
                                native_meeting_id="aaa-bbbb-ccc", **KW))
    _ended(repo, m["id"], ago_s=120)
    from meeting_api.bot_spawn.ports import SeatCooldown

    with pytest.raises(SeatCooldown):
        asyncio.run(request_bot(repo, runtime, user_id=USER, platform="google_meet",
                                native_meeting_id="ddd-eeee-fff", seat_cooldown=True, **KW))


def test_last_minute_is_measured_from_when_scheduled_at_was_last_set():
    """L3: a plan created days ago and moved to "now" is a last-minute booking."""
    from meeting_api.bot_spawn.auto_join import is_last_minute_booking

    moved = {"created_at": "2026-11-01T10:00:00Z",
             "data": {"scheduled_at": "2026-11-04T10:01:00Z",
                      "scheduled_at_set_at": "2026-11-04T10:00:00Z"}}
    assert is_last_minute_booking(moved)
    planned = {"created_at": "2026-11-04T09:59:00Z",
               "data": {"scheduled_at": "2026-11-04T10:01:00Z",
                        "scheduled_at_set_at": "2026-11-03T10:00:00Z"}}
    assert not is_last_minute_booking(planned), "set a day ahead, whatever created_at says"


def test_every_scheduled_at_write_stamps_when_it_was_set():
    from meeting_api.collector.fakes import InMemoryTranscriptStore

    store = InMemoryTranscriptStore()
    row = asyncio.run(store.create_planned_meeting(USER, platform="google_meet",
                                                   native_meeting_id="aaa-bbbb-ccc",
                                                   scheduled_at="2026-11-10T10:00:00Z"))
    first = row["data"]["scheduled_at_set_at"]
    asyncio.run(store.set_intent(USER, "google_meet", "aaa-bbbb-ccc", "scheduled",
                                 scheduled_at="2026-11-11T10:00:00Z"))
    data = next(m for m in store._meetings.values())["data"]
    assert data["scheduled_at_set_at"] >= first
    asyncio.run(store.set_intent(USER, "google_meet", "aaa-bbbb-ccc", "idle"))
    data = next(m for m in store._meetings.values())["data"]
    assert "scheduled_at" not in data and "scheduled_at_set_at" not in data


# ── 3. one queue-wait source ────────────────────────────────────────────────────────────────────

def test_the_reaper_ends_a_timed_out_queue_with_the_rows_own_wait():
    from meeting_api.lifecycle.reconcile import synthesize_terminal_for_dead_workload

    repo = InMemoryMeetingRepo()
    m = asyncio.run(repo.create_meeting(user_id=USER, platform="google_meet",
                                        native_meeting_id="aaa-bbbb-ccc",
                                        data={"queue_wait_s": 600,
                                              "queue": {"queued_at": "2026-11-04T10:00:00Z",
                                                        "timing_out": "2026-11-04T10:10:00Z"}}))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid="s-1"))
    asyncio.run(repo.set_bot_container(meeting_id=m["id"], bot_container_id="w-1"))
    repo._meetings[m["id"]]["status"] = "queued"
    posted: list[dict] = []

    async def drive(body):
        posted.append(body)
        return 200

    class _Log:
        def __getattr__(self, name):
            return lambda *a, **k: None

    assert asyncio.run(synthesize_terminal_for_dead_workload(repo, "w-1", "destroyed", drive, log=_Log()))
    assert posted[0]["completion_reason"] == "cluster_at_capacity"
    assert "10 min" in posted[0]["reason"], posted[0]["reason"]


def test_the_queue_wait_env_is_not_a_second_source(monkeypatch):
    """VEXA_QUEUE_MAX_WAIT_S is gone: a run without a stamped wait uses the built-in default."""
    from test_capacity_queue import T0, _Log, _unschedulable, _world
    from meeting_api.lifecycle.machine import TransitionSource
    from meeting_api.lifecycle.queue import capacity_queue_sweep

    monkeypatch.setenv("VEXA_QUEUE_MAX_WAIT_S", "60")
    repo, meeting, runtime, sink, app = _world(pending=_unschedulable())

    async def _time_out(body):
        code, _ = await app.state.apply_lifecycle_event(
            body, transition_source=TransitionSource.CAPACITY_QUEUE, force_terminal_on_destroy=True)
        return code

    for at in (T0, T0 + timedelta(minutes=5)):
        asyncio.run(capacity_queue_sweep(repo, runtime, enter=app.state.apply_queue_transition,
                                         time_out=_time_out, log=_Log(), now=at, scale_up_window=300))
    assert repo._meetings[meeting["id"]]["status"] == "queued"


# ── 4. L1: the timing_out stamp means "teardown requested", retried until the workload is gone ──

def test_a_requested_teardown_is_retried_even_when_a_seat_comes_after():
    from test_capacity_queue import T0, WORKLOAD, _sweep, _unschedulable, _world

    repo, meeting, fake, sink, app = _world(pending=_unschedulable())

    class _FlakyDelete(FakeRuntimeClient):
        fails = 1

        async def delete_workload(self, workload_id):
            if self.fails:
                self.fails -= 1
                raise RuntimeError("runtime timed out")
            await super().delete_workload(workload_id)

    runtime = _FlakyDelete(workloads=fake._workloads)
    _sweep(app, repo, runtime, now=T0)
    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=30))
    assert repo._meetings[meeting["id"]]["data"]["queue"].get("timing_out"), "teardown requested"
    runtime._workloads[WORKLOAD]["pending"] = _unschedulable(autoscaler="scale_up")   # a node at last

    _sweep(app, repo, runtime, now=T0 + timedelta(minutes=30, seconds=15))

    row = repo._meetings[meeting["id"]]
    assert runtime.deleted == [WORKLOAD], "the requested teardown is carried through"
    assert row["status"] == "failed" and row["data"]["completion_reason"] == "cluster_at_capacity"


# ── 5. one vocabulary: machine.py defines every status set ────────────────────────────────────

_STATUS_WORDS = {"idle", "scheduled", "requested", "queued", "joining", "awaiting_admission",
                 "needs_help", "needs_human_help", "active", "stopping", "completed", "failed"}


def test_no_status_set_is_written_by_hand_outside_machine():
    import ast
    from pathlib import Path

    import meeting_api

    root = Path(meeting_api.__file__).parent
    hand_made = []
    for path in root.rglob("*.py"):
        if path.name == "machine.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and len(node.elts) >= 2:
                words = [e.value for e in node.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if len(words) == len(node.elts) and set(words) <= _STATUS_WORDS:
                    hand_made.append(f"{path.relative_to(root)}:{node.lineno} {tuple(words)}")
    assert hand_made == [], "derive these from lifecycle/machine.py:\n" + "\n".join(hand_made)


def test_a_status_machine_adds_is_seen_by_every_set_derived_from_it():
    from meeting_api.lifecycle import machine

    assert machine.QUEUED in machine.BOOTING_STATUSES
    for name in ("SEAT_HOLDING_STATUSES", "RUNNING_STATUSES", "IN_FLIGHT_STATUSES",
                 "LIVE_STATUSES", "LIVENESS_GATED_STATUSES", "NON_TERMINAL_STATUSES"):
        derived = getattr(machine, name)
        assert set(machine.BOOTING_STATUSES) <= set(derived), name
    assert not set(machine.NON_TERMINAL_STATUSES) & set(machine.TERMINAL_STATUSES)
    assert set(machine.ALL_STATUSES) == _STATUS_WORDS


def test_cluster_at_capacity_is_defined_once():
    from meeting_api.lifecycle import join_evidence, machine, queue

    assert machine.StatusReason.CLUSTER_AT_CAPACITY.value == machine.CompletionReason.CLUSTER_AT_CAPACITY.value
    assert join_evidence.JoinFailureReason.CLUSTER_AT_CAPACITY.value == machine.CompletionReason.CLUSTER_AT_CAPACITY.value
    assert not hasattr(queue, "COMPLETION_REASON") and not hasattr(queue, "REASON_CLUSTER_AT_CAPACITY")
