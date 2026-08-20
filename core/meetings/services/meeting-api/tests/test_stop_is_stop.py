""""Explicit stop must be stop, evict" — the four seams the rev-193 storm opened (F1-F4).

Founder ruling 2026-08-17. Each test below reproduces ONE witnessed failure from stage rev 193
(evidence 02:5x-03:0x, 2026-08-18) against the SAME shipped code paths production runs, with the
in-memory fakes standing in for Postgres and the runtime kernel:

  **F1** row 26306 — DELETE on a still-``scheduled`` calendar occurrence answered 200 and set
  ``data.stop_requested``; the auto-join sweep dispatched it anyway (pod 02:52:16, bot joined). The
  stop landed between two rules that could not see it: the sweep's due-filter is a RATE rule, and
  ``lifecycle.occurrence``'s eligibility rule only classifies TERMINAL rows.

  **F2** row 26313 — DELETE 0.4s after POST answered 200 and flagged the row; the pod was created
  AFTERWARDS and ran. The stop's direct teardown targeted a workload that did not exist yet, and the
  leave command was published before any bot could subscribe.

  **F3** — a stop that lost either race terminalized as ``join_failure`` (TRANSIENT in ``retry.py``)
  while ``stop_requested`` was true on the row, leaving the occurrence eligible for re-dispatch.

  **F4** — ``POST /bots {"continue_meeting": true}`` reopened ANY terminal row in place, erasing its
  completion reason — including a USER-STOPPED one (26313 resurrected to ``requested`` with
  ``stop_requested`` still true).

The race tests drive the REAL interleaving rather than asserting on a flag: the DELETE is fired from
inside the spawn path at each point the storm could have hit, through the app over ASGI, in one event
loop. A fix that merely narrowed the window would still fail them at the later race points.
"""
from __future__ import annotations

import httpx
import pytest

from meeting_api import create_app
from meeting_api.bot_spawn.auto_join import auto_join_tick, due_rows
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.ports import MeetingStopped
from meeting_api.bot_spawn.service import request_bot
from meeting_api.lifecycle.machine import dominant_completion_reason
from meeting_api.lifecycle.occurrence import Disposition, disposition, may_dispatch_again
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher

USER = 7
PLATFORM = "google_meet"
NATIVE = "abc-defg-hij"
WHEN = "2026-08-18T02:52:00+00:00"
LIFECYCLE = "/bots/internal/callback/lifecycle"


@pytest.fixture(autouse=True)
def _token_secret(monkeypatch):
    """POST /bots mints a MeetingToken signed with ADMIN_TOKEN; the HTTP paths below need one."""
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")


def _at(offset_s: int = 0):
    from datetime import datetime, timedelta, timezone

    return datetime(2026, 8, 18, 2, 52, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


def _app(repo, runtime=None, publisher=None):
    return create_app(
        meeting_repo=repo,
        runtime=runtime if runtime is not None else FakeRuntimeClient(),
        command_publisher=publisher if publisher is not None else InMemoryCommandPublisher(),
    )


def _client(app) -> httpx.AsyncClient:
    """An ASGI client usable from INSIDE a running loop — the race tests fire the DELETE from within
    the spawn path, which ``TestClient`` (sync, spins its own loop) cannot do."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _seed_scheduled(repo, *, native=NATIVE, data=None) -> dict:
    """A PLANNED calendar occurrence: a row with a time and no bot (what sync creates)."""
    row = await repo.create_meeting(
        user_id=USER, platform=PLATFORM, native_meeting_id=native,
        data={"scheduled_at": WHEN, "auto_join": True, "calendar_uid": "uid-1", **(data or {})},
    )
    repo.set_status(row["id"], "scheduled")
    return await repo.get_meeting(row["id"])


async def _sweep(repo, runtime, at):
    return await auto_join_tick(
        repo, runtime, transcribe_gate=lambda: None, now=at,
        token_secret="s", redis_url="redis://r", allow_uncapped=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# F1 — a stop on a SCHEDULED occurrence: never dispatched, and terminal immediately
# ═══════════════════════════════════════════════════════════════════════════════════════════════

async def test_f1_stopping_a_scheduled_occurrence_terminalizes_it_and_no_bot_is_dispatched():
    """Row 26306, exactly: stop a scheduled occurrence, then run the sweep in its own due window.

    Two assertions, and the second is the one rev 193 failed: the row must be TERMINAL (a flagged
    ``scheduled`` row is a zombie no rule owns), and the sweep must send nothing."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    planned = await _seed_scheduled(repo)

    async with _client(_app(repo, runtime)) as client:
        r = await client.delete(f"/bots/{PLATFORM}/{NATIVE}", headers={"x-user-id": str(USER)})

    assert r.status_code == 200, r.text
    assert r.json()["cancelled"] == [planned["id"]], (
        "the response must name the plan it CANCELLED — a caller cannot tell 'asked a bot to leave' "
        "from 'called off a meeting that had none' otherwise"
    )

    row = await repo.get_meeting(planned["id"])
    assert row["status"] == "failed", (
        "a stopped plan must be TERMINAL. Left `scheduled`, it falls between the sweep's rate rule "
        "and occurrence.py's eligibility rule — which is how 26306 was dispatched at 02:52:16"
    )
    assert row["data"]["completion_reason"] == "stopped"
    assert row["data"]["stop_requested"] is True
    assert "failure_stage" not in row["data"], (
        "no bot ever ran, so there is no stage to attribute; stamping one would claim a spawn that "
        "never happened"
    )
    assert disposition(row) is Disposition.USER_STOPPED
    assert not may_dispatch_again(row)

    # The sweep, in the exact window the storm fired in.
    counters = await _sweep(repo, runtime, _at(0))
    assert counters["due"] == 0 and counters["spawned"] == 0
    assert runtime.specs == [], "a bot went into a meeting the user had already called off"


async def test_f1_a_flagged_scheduled_row_is_never_due():
    """The standing guarantee, independent of who wrote the row.

    Post-fix the stop terminalizes the plan, so this shape should not exist — but rev-193 rows do
    exist, and a row carrying the user's stop must never be due whatever left it that way. Asserted
    on the PURE filter so it holds for every caller of ``due_rows``."""
    zombie = {
        "id": 26306, "user_id": USER, "platform": PLATFORM, "native_meeting_id": NATIVE,
        "status": "scheduled",
        "data": {"scheduled_at": WHEN, "auto_join": True, "stop_requested": True},
    }
    assert due_rows([zombie], now=_at(0)) == []
    # ...and the identical row WITHOUT the flag is due — so the test proves the flag is the cause,
    # not the window.
    ok = {**zombie, "data": {k: v for k, v in zombie["data"].items() if k != "stop_requested"}}
    assert due_rows([ok], now=_at(0)) == [ok]


async def test_f1_an_explicit_new_post_still_works_on_a_stopped_room():
    """The stop ends THAT occurrence, never the room. A user who stops a scheduled meeting and then
    asks for a bot must get one — otherwise the fix trades a false positive for a dead product."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    await _seed_scheduled(repo)
    async with _client(_app(repo, runtime)) as client:
        await client.delete(f"/bots/{PLATFORM}/{NATIVE}", headers={"x-user-id": str(USER)})
        r = await client.post(
            "/bots", headers={"x-user-id": str(USER)},
            json={"platform": PLATFORM, "native_meeting_id": NATIVE},
        )
    assert r.status_code == 201, r.text
    assert len(runtime.specs) == 1


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# F2 — a stop that RACES the spawn: no pod survives, at any race point
# ═══════════════════════════════════════════════════════════════════════════════════════════════

class _StopRacingRepo(InMemoryMeetingRepo):
    """A repo that fires the user's DELETE from INSIDE the spawn, at a chosen point.

    This is the storm's shape, not a simulation of it: rev 193's DELETE landed 0.4s after the POST,
    i.e. while the spawn was between its own steps. Racing at each step in turn is what distinguishes
    a fence that closed the window from one that merely moved it."""

    def __init__(self, *, at: str):
        super().__init__()
        self.at = at
        self.stop_response = None
        self._client = None

    async def _fire_stop(self, when: str) -> None:
        if when != self.at or self.stop_response is not None:
            return
        self.stop_response = await self._client.delete(
            f"/bots/{PLATFORM}/{NATIVE}", headers={"x-user-id": str(USER)}
        )

    async def create_meeting_guarded(self, **kw):
        row = await super().create_meeting_guarded(**kw)
        await self._fire_stop("after_insert")
        return row

    async def create_session(self, **kw):
        await super().create_session(**kw)
        await self._fire_stop("after_session")

    async def set_bot_container(self, **kw):
        row = await super().set_bot_container(**kw)
        await self._fire_stop("after_container")
        return row


class _StopRacingRuntime(FakeRuntimeClient):
    """Fires the DELETE from inside ``create_workload`` — the pod is coming up as the user stops."""

    def __init__(self, repo: _StopRacingRepo):
        super().__init__()
        self._repo = repo

    async def create_workload(self, spec):
        result = await super().create_workload(spec)
        await self._repo._fire_stop("during_create")
        return result


@pytest.mark.parametrize(
    "race_point,pod_expected",
    [
        # Before the workload exists → the pre-spawn fence refuses, and NO pod is ever created.
        ("after_insert", False),
        # The pod is coming up as the stop lands. Nothing can prevent its creation — the guarantee
        # is that it does not survive. This is rev 193's exact interleaving: the stop could not see
        # a container id (not written yet), so only the spawn side can catch it.
        ("during_create", True),
        ("after_session", True),
        # The container id is committed before the spawn reads the flag; the stop's own re-read sees
        # it too. Either side may do the teardown — the assertion is that one of them did.
        ("after_container", True),
    ],
)
async def test_f2_a_stop_racing_the_spawn_never_leaves_a_bot_running(race_point, pod_expected):
    repo = _StopRacingRepo(at=race_point)
    runtime = _StopRacingRuntime(repo)
    app = _app(repo, runtime)

    async with _client(app) as client:
        repo._client = client
        with pytest.raises(MeetingStopped):
            await request_bot(
                repo, runtime, user_id=USER, platform=PLATFORM, native_meeting_id=NATIVE,
                token_secret="s", redis_url="redis://r",
            )

    assert repo.stop_response is not None and repo.stop_response.status_code == 200, (
        "the DELETE itself must still succeed — the user pressed stop and it worked"
    )
    assert bool(runtime.specs) is pod_expected, (
        f"race at {race_point}: pod creation expectation not met"
    )
    if pod_expected:
        # AT LEAST one side tore it down. At the last race point BOTH do — the stop's fresh re-read
        # sees the committed container id and the spawn's read sees the committed flag — and that
        # is the interlock working, not a defect: a redundant teardown is idempotent, whereas a
        # schedule in which NEITHER side sees the other is the bug (rev 193, row 26313).
        assert set(runtime.deleted) == {runtime.specs[0]["workloadId"]}, (
            f"race at {race_point}: a pod was created and NOT torn down — this is rev 193's row "
            f"26313, where the bot joined a meeting the user had already stopped"
        )

    # And the row tells the truth about why it ended, so nothing re-dispatches the occurrence.
    row = (await repo.find_latest(USER, PLATFORM, NATIVE))
    assert row["status"] == "failed"
    assert row["data"]["completion_reason"] == "stopped"
    assert disposition(row) is Disposition.USER_STOPPED


async def test_f2_the_stop_route_rereads_the_container_it_is_tearing_down():
    """The stop's half of the interlock, isolated.

    ``rows`` is a snapshot taken before the stop writes anything; a spawn racing it writes
    ``bot_container_id`` after that snapshot. Reading the container fresh — AFTER the
    ``stop_requested`` write commits — is what makes the two-sided interlock hole-free. Here the
    container appears only after the snapshot, so a route reading the snapshot tears down nothing."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    row = await repo.create_meeting(
        user_id=USER, platform=PLATFORM, native_meeting_id=NATIVE, data={}
    )
    await repo.create_session(meeting_id=row["id"], session_uid="sess-1")
    snapshot_taken = {"done": False}
    real_rows = repo.find_active_rows

    async def find_active_rows(*a, **kw):
        found = await real_rows(*a, **kw)          # the pre-write snapshot: container still None
        if not snapshot_taken["done"]:
            snapshot_taken["done"] = True
            await repo.set_bot_container(meeting_id=row["id"], bot_container_id="wl-race")
        return found

    repo.find_active_rows = find_active_rows

    async with _client(_app(repo, runtime)) as client:
        r = await client.delete(f"/bots/{PLATFORM}/{NATIVE}", headers={"x-user-id": str(USER)})

    assert r.status_code == 200, r.text
    assert runtime.deleted == ["wl-race"], (
        "the stop tore down nothing: it decided off a snapshot older than its own write, which is "
        "the half of the F2 interlock that failed on rev 193"
    )


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# F3 — the user's intent dominates the reason, whichever fault won the race
# ═══════════════════════════════════════════════════════════════════════════════════════════════

async def test_f3_a_lost_race_records_stopped_not_the_fault_it_hit():
    """The bot, killed mid-join, reports what IT saw (``join_failure``). The row must record what
    the USER did.

    ``join_failure`` is TRANSIENT in ``retry.py``, so the mis-attributed row stayed eligible for
    re-dispatch — a stop that produced a retry. Driven through the real callback endpoint, so the
    dominance is asserted where the value is actually persisted."""
    repo = InMemoryMeetingRepo()
    row = await repo.create_meeting(
        user_id=USER, platform=PLATFORM, native_meeting_id=NATIVE, data={},
    )
    await repo.create_session(meeting_id=row["id"], session_uid="sess-1")
    repo.set_status(row["id"], "joining")
    # What DELETE writes for a PRE-ACTIVE row: the stage survives (#807), the intent rides in data.
    await repo.merge_meeting_data(row["id"], {"stop_requested": True})

    async with _client(_app(repo)) as client:
        # The FSM's first event must be `joining`; then the bot's own terminal, blaming the network.
        await client.post(LIFECYCLE, json={"connection_id": "sess-1", "status": "joining"})
        r = await client.post(LIFECYCLE, json={
            "connection_id": "sess-1", "status": "failed",
            "completion_reason": "join_failure", "exit_code": 1,
        })

    assert r.status_code == 200, r.text
    assert r.json()["completion_reason"] == "stopped"
    final = await repo.get_meeting(row["id"])
    assert final["data"]["completion_reason"] == "stopped", (
        "the row recorded the fault the race happened to reach first, not the user's intent"
    )
    assert final["data"]["failure_stage"] == "joining", "the stage is still attributed truthfully"
    assert disposition(final) is Disposition.USER_STOPPED
    assert not may_dispatch_again(final)


def test_f3_dominance_is_one_rule_used_by_every_writer():
    """The unit behind it: no writer gets to have its own opinion."""
    from meeting_api.lifecycle.reconcile import _pre_active_completion_reason

    assert dominant_completion_reason("join_failure", stop_requested=True) == "stopped"
    assert dominant_completion_reason("join_failure", stop_requested=False) == "join_failure"
    assert dominant_completion_reason(None, stop_requested=True) == "stopped"
    for status in ("requested", "joining", "awaiting_admission", None):
        assert _pre_active_completion_reason(status, True) == "stopped"


async def test_f3_fail_meeting_cannot_write_a_fault_over_a_stop():
    """The by-id terminal writer (the spawn-failure path) obeys the same rule — a spawn that fails
    for its own reasons on a row the user has already stopped still records the stop."""
    repo = InMemoryMeetingRepo()
    row = await repo.create_meeting(
        user_id=USER, platform=PLATFORM, native_meeting_id=NATIVE, data={"stop_requested": True},
    )
    await repo.fail_meeting(meeting_id=row["id"], reason="kernel said no")
    assert (await repo.get_meeting(row["id"]))["data"]["completion_reason"] == "stopped"


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# F4 — continue_meeting never resurrects a stopped run, and never erases how a run ended
# ═══════════════════════════════════════════════════════════════════════════════════════════════

async def _seed_terminal(repo, *, data) -> dict:
    row = await repo.create_meeting(
        user_id=USER, platform=PLATFORM, native_meeting_id=NATIVE, data=data,
    )
    repo.set_status(row["id"], "failed")
    return await repo.get_meeting(row["id"])


async def test_f4_continue_meeting_refuses_a_user_stopped_row():
    """Row 26313's resurrection. ``continue_meeting`` reopens a terminal row IN PLACE — no advisory
    lock, no dedup, and it cleared the terminal attribution on the way — so it was the one path back
    into a run the user had ended."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    stopped = await _seed_terminal(
        repo, data={"stop_requested": True, "completion_reason": "stopped"},
    )

    async with _client(_app(repo, runtime)) as client:
        r = await client.post(
            "/bots", headers={"x-user-id": str(USER)},
            json={"platform": PLATFORM, "native_meeting_id": NATIVE, "continue_meeting": True},
        )

    assert r.status_code == 409, r.text
    assert "stopped by the user" in r.json()["detail"]
    assert "POST /bots again" in r.json()["detail"], (
        "the refusal must name the path that works — a stopped meeting is finished, not broken"
    )
    assert runtime.specs == [], "a bot was started for a resurrected stopped row"
    after = await repo.get_meeting(stopped["id"])
    assert after["status"] == "failed" and after["data"]["completion_reason"] == "stopped", (
        "the refused request must leave the stopped row exactly as it was"
    )


async def test_f4_a_fresh_post_after_a_stop_starts_a_new_run_on_a_new_row():
    """The documented path the 409 points at, proven to work — otherwise the refusal is a dead end."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    stopped = await _seed_terminal(
        repo, data={"stop_requested": True, "completion_reason": "stopped"},
    )

    async with _client(_app(repo, runtime)) as client:
        r = await client.post(
            "/bots", headers={"x-user-id": str(USER)},
            json={"platform": PLATFORM, "native_meeting_id": NATIVE},
        )

    assert r.status_code == 201, r.text
    assert r.json()["id"] != stopped["id"], "a new run is a new row; the stopped row stays stopped"
    assert (await repo.get_meeting(stopped["id"]))["data"]["completion_reason"] == "stopped"


async def test_f4_reopen_preserves_how_the_previous_run_ended():
    """A continued run is a SECOND run on one row, so the first run's ending is history — archived,
    not erased. Erasing it is why a resurrected row read as one that had never ended."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    prior = await _seed_terminal(
        repo, data={"completion_reason": "left_alone", "failure_stage": "active"},
    )

    async with _client(_app(repo, runtime)) as client:
        r = await client.post(
            "/bots", headers={"x-user-id": str(USER)},
            json={"platform": PLATFORM, "native_meeting_id": NATIVE, "continue_meeting": True},
        )

    assert r.status_code == 201, r.text
    row = await repo.get_meeting(prior["id"])
    assert row["status"] == "requested" and row["id"] == prior["id"]
    assert "completion_reason" not in row["data"], "the LIVE attribution is the new run's, not the old"
    (archived,) = row["data"]["completion_history"]
    assert archived["completion_reason"] == "left_alone"
    assert archived["failure_stage"] == "active"
    assert archived["reopened_at"]


async def test_f4_the_repo_refuses_the_reopen_too():
    """Defense in depth under the service's 409: no caller reopens a stopped row, ever."""
    repo = InMemoryMeetingRepo()
    stopped = await _seed_terminal(repo, data={"stop_requested": True})
    with pytest.raises(MeetingStopped):
        await repo.reopen_meeting(meeting_id=stopped["id"])
