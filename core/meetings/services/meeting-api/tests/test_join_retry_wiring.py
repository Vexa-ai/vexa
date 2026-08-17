"""#1190 — the WIRING eval: a terminal `failed` meeting actually produces a bounded re-spawn.

`test_join_retry.py` proves the controller in isolation; this proves the two seams that were
missing, which is the whole content of #1190:

  * the TRIGGER — `JoinRetryCoordinator` reading its decision inputs off the DURABLE meeting row
    (sealed `completion_reason`, #1075's `join_evidence`, the attempt counter) and persisting the
    next attempt number, so a chain of terminals produces attempts 1, 2, … and then stops;
  * the DISPATCH — the scheduled job carrying a real `POST /bots` re-spawn with `continue_meeting`
    set, so each attempt is a FRESH `meeting_session` on the SAME meeting row.

Plus the app-level leg: the shipped lifecycle callback calls the port on a real `failed` advance and
NOT on an idempotent redelivery (the bot retries its terminal callback 3x — each replay must not buy
another bot).

OFFLINE — `FakeClock` + the mirrored `Scheduler` + a capturing dispatch + `InMemoryMeetingRepo`.
No bot, no docker, no network.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle import (
    JOIN_RETRY_DATA_KEY,
    CompletionReason,
    JoinFailureReason,
    JoinRetryController,
    JoinRetryCoordinator,
    RetryPolicy,
)
from meeting_api.scheduling import FakeClock, Scheduler

ENDPOINT = "/bots/internal/callback/lifecycle"


# ── harness ─────────────────────────────────────────────────────────────────────────────────────


class _Rig:
    """A coordinator over a FakeClock-gated scheduler + a capturing dispatch + a real repo row.

    `fail(...)` plays ONE terminal failure onto the row exactly as the lifecycle callback would
    (stamping the terminal attribution into `meeting.data` first), then advances the clock past the
    backoff and ticks — so `fired` holds the re-spawn requests a deployment would really have sent.
    """

    def __init__(self, *, max_attempts=3, backoff=(30.0, 120.0, 300.0),
                 retry_admission_timeout=False, enabled=True):
        self.repo = InMemoryMeetingRepo()
        self.clock = FakeClock(start=1000.0)
        self.fired: list[dict] = []
        self.sessions = 0

        def dispatch(request):
            # Stand in for the production dispatch's in-process `request_bot(continue_meeting=True)`:
            # a NEW session on the SAME meeting row, exactly as a real re-spawn mints.
            self.sessions += 1
            uid = f"sess-retry-{request['body']['attempt']}"
            asyncio.run(self.repo.create_session(meeting_id=self.row["id"], session_uid=uid))
            self.fired.append({"request": request, "session_uid": uid})
            return {"status": "spawned", "session_uid": uid}

        self.scheduler = Scheduler(dispatch=dispatch, clock=self.clock)
        self.coordinator = JoinRetryCoordinator(
            JoinRetryController(
                self.scheduler,
                policy=RetryPolicy(
                    max_attempts=max_attempts, backoff=list(backoff),
                    retry_admission_timeout=retry_admission_timeout,
                ),
            ),
            self.repo,
            meeting_api_url="http://meeting-api:8080",
            enabled=enabled,
        )
        self.row = asyncio.run(self.repo.create_meeting(
            user_id=7, platform="teams", native_meeting_id="19:meeting_abc",
            data={"constructed_meeting_url": "https://teams.microsoft.com/l/meetup-join/19:meeting_abc"},
        ))

    def _row(self) -> dict:
        """The row as the lifecycle callback would hand it over: a fresh dict, live `data`."""
        return asyncio.run(self.repo.find_latest(7, "teams", "19:meeting_abc"))

    def fail(self, completion_reason, *, evidence=None, stage="joining", stop_requested=False):
        """Stamp one terminal `failed` attribution onto the row and run the coordinator on it."""
        patch = {"completion_reason": getattr(completion_reason, "value", completion_reason),
                 "failure_stage": stage}
        if evidence is not None:
            patch["join_evidence"] = evidence
        if stop_requested:
            patch["stop_requested"] = True
        asyncio.run(self.repo.merge_meeting_data(self.row["id"], patch))
        self.repo.set_status(self.row["id"], "failed")
        return asyncio.run(self.coordinator.on_terminal_failed(self._row()))

    def advance_and_tick(self, seconds: float) -> int:
        self.clock.advance(seconds)
        return self.scheduler.tick()

    @property
    def stored_attempt(self) -> int:
        block = (self._row().get("data") or {}).get(JOIN_RETRY_DATA_KEY) or {}
        return block.get("attempt", 0)


def _evidence(reason: JoinFailureReason, **extra) -> dict:
    return {"reason": reason.value, "attribution": "system_fault", "source": "bot", **extra}


# ── transient: bounded re-spawns at 30/120/300, each a fresh session on the same row ─────────────


def test_transient_join_failure_respawns_at_30s():
    rig = _Rig()
    out = rig.fail(CompletionReason.JOIN_FAILURE)

    assert out.action == "scheduled_retry"
    assert out.next_at == 1000.0 + 30.0
    assert rig.stored_attempt == 1, "the attempt counter must be DURABLE — the row is what survives"

    assert rig.advance_and_tick(29) == 0 and rig.fired == []   # not due yet
    assert rig.advance_and_tick(1) == 1                        # due → one re-spawn
    request = rig.fired[0]["request"]
    assert request["method"] == "POST"
    assert request["url"].endswith("/bots")
    body = request["body"]
    assert body["attempt"] == 1
    assert body["continue_meeting"] is True, "a retry REUSES the meeting row and mints a new session"
    assert body["platform"] == "teams"
    assert body["native_meeting_id"] == "19:meeting_abc"
    assert body["meeting_url"].endswith("19:meeting_abc")
    assert request["headers"]["x-user-id"] == "7"


def test_bounded_to_three_attempts_with_the_backoff_schedule():
    """Attempt 0 is the original spawn: max_attempts=3 buys exactly TWO re-spawns, at 30s then 120s,
    and the third terminal is exhausted — no fourth bot, ever."""
    rig = _Rig(max_attempts=3)

    assert rig.fail(CompletionReason.JOIN_FAILURE).action == "scheduled_retry"
    assert rig.advance_and_tick(30) == 1

    second = rig.fail(CompletionReason.JOIN_FAILURE)
    assert second.action == "scheduled_retry"
    assert second.next_at == rig.clock.now() + 120.0
    assert rig.advance_and_tick(120) == 1

    third = rig.fail(CompletionReason.JOIN_FAILURE)
    assert third.action == "exhausted"

    assert [f["request"]["body"]["attempt"] for f in rig.fired] == [1, 2]
    assert len({f["session_uid"] for f in rig.fired}) == 2, "each attempt is its OWN session"
    assert rig.advance_and_tick(10_000) == 0, "exhausted means exhausted"


def test_success_on_attempt_two_stops_the_loop():
    """The loop is terminal-driven: a successful re-spawn produces no further `failed` terminal, so
    nothing calls the coordinator again and no job is left pending."""
    rig = _Rig()
    rig.fail(CompletionReason.JOIN_FAILURE)
    assert rig.advance_and_tick(30) == 1
    # attempt 1 joined — no terminal failure arrives
    assert rig.scheduler.list(status="pending") == []
    assert rig.advance_and_tick(10_000) == 0
    assert len(rig.fired) == 1


# ── the traps: what must NEVER re-spawn ─────────────────────────────────────────────────────────


def test_redirect_shaped_failure_never_retries():
    """#1190's binding constraint. `teams_auth_redirect` lands as a sealed `join_failure` (transient)
    and is a hard tenant policy; #1075's evidence types it `navigation_failure` and the wiring
    refuses. Wiring it without this guard would have re-spawned 3x against a wall."""
    rig = _Rig()
    out = rig.fail(
        CompletionReason.JOIN_FAILURE,
        evidence=_evidence(JoinFailureReason.NAVIGATION_FAILURE,
                           detail="TeamsJoinRedirectError: teams_auth_redirect"),
    )
    assert out.action == "permanent"
    assert rig.stored_attempt == 0
    assert rig.advance_and_tick(10_000) == 0
    assert rig.fired == []


def test_rejected_never_retries():
    rig = _Rig()
    out = rig.fail(CompletionReason.AWAITING_ADMISSION_REJECTED, stage="awaiting_admission")
    assert out.action == "permanent"
    assert rig.advance_and_tick(10_000) == 0
    assert rig.fired == []


@pytest.mark.parametrize("reason", [
    CompletionReason.EVICTED,
    CompletionReason.AUTH_SESSION_MISSING,
    CompletionReason.STOPPED,
    CompletionReason.VALIDATION_ERROR,
    CompletionReason.MAX_BOT_TIME_EXCEEDED,
])
def test_permanent_reasons_never_retry(reason):
    rig = _Rig()
    assert rig.fail(reason).action == "permanent"
    assert rig.advance_and_tick(10_000) == 0


def test_admission_timeout_never_retries_by_default():
    """Founder ruling 2026-08-17 — the bot already stood at the door for the whole lobby budget."""
    rig = _Rig()
    out = rig.fail(
        CompletionReason.AWAITING_ADMISSION_TIMEOUT, stage="awaiting_admission",
        evidence=_evidence(JoinFailureReason.ADMISSION_TIMEOUT),
    )
    assert out.action == "permanent"
    assert rig.advance_and_tick(10_000) == 0
    assert rig.fired == []


def test_admission_timeout_retries_only_behind_the_config_arm():
    rig = _Rig(retry_admission_timeout=True)
    out = rig.fail(CompletionReason.AWAITING_ADMISSION_TIMEOUT, stage="awaiting_admission")
    assert out.action == "scheduled_retry"
    assert rig.advance_and_tick(30) == 1


def test_post_admission_failure_is_not_a_join_failure():
    """A bot that reached `active` was ADMITTED; whatever killed it afterwards is not a join
    failure and #1075 files no join evidence for it — the coordinator refuses the row outright."""
    rig = _Rig()
    assert rig.fail(CompletionReason.JOIN_FAILURE, stage="active") is None
    assert rig.advance_and_tick(10_000) == 0


def test_user_stop_marker_blocks_a_respawn():
    rig = _Rig()
    assert rig.fail(CompletionReason.JOIN_FAILURE, stop_requested=True) is None
    assert rig.fired == []


def test_disabled_is_a_total_no_op():
    rig = _Rig(enabled=False)
    assert rig.fail(CompletionReason.JOIN_FAILURE) is None
    assert rig.advance_and_tick(10_000) == 0
    assert rig.stored_attempt == 0


def test_duplicate_terminal_for_the_same_attempt_is_idempotent():
    """The idempotency key `join_retry:{meeting_id}:{attempt}` means a doubled decision for the same
    attempt cannot buy two bots — the belt to the callback's own no-op replay gate."""
    rig = _Rig()
    a = rig.fail(CompletionReason.JOIN_FAILURE)
    # replay the SAME attempt (the counter has moved, so force the original attempt number back)
    asyncio.run(rig.repo.merge_meeting_data(rig.row["id"], {JOIN_RETRY_DATA_KEY: {"attempt": 0}}))
    b = rig.fail(CompletionReason.JOIN_FAILURE)
    assert a.job_id == b.job_id
    assert len(rig.scheduler.list(status="pending")) == 1
    assert rig.advance_and_tick(30) == 1


# ── the app leg: the shipped lifecycle callback calls the port ──────────────────────────────────


def _seeded_app(calls: list, *, session_uid="sess-uid"):
    repo = InMemoryMeetingRepo()
    row = asyncio.run(repo.create_meeting(
        user_id=1, platform="google_meet", native_meeting_id="m1", data={},
    ))
    asyncio.run(repo.create_session(meeting_id=row["id"], session_uid=session_uid))
    repo.set_status(row["id"], "joining")

    async def _join_retry(meeting_row):
        calls.append(meeting_row)

    return TestClient(create_app(meeting_repo=repo, join_retry=_join_retry)), repo, row


def test_failed_terminal_invokes_the_join_retry_port(goldens):
    calls: list = []
    client, repo, row = _seeded_app(calls)

    r = client.post(ENDPOINT, json=goldens["failed-join-evidence"])

    assert r.status_code == 200, r.text
    assert len(calls) == 1, "a terminal `failed` advance must reach the retry port"
    handed = calls[0]
    assert handed["id"] == row["id"]
    # The port is handed the PERSISTED row, so it decides on the same inputs a human would query.
    assert handed["data"]["completion_reason"] == "awaiting_admission_timeout"
    assert handed["data"]["join_evidence"]["reason"] == "admission_timeout"
    # FM-003: the stage is derived server-side from the FSM's own state (the row was `joining`),
    # never read off the payload — the retry decision inherits that discipline for free.
    assert handed["data"]["failure_stage"] == "joining"


def test_terminal_redelivery_does_not_buy_a_second_retry(goldens):
    """The bot retries its terminal callback up to 3x. Each replay is an idempotent no-op advance —
    and must not re-enter the retry decision, or one failure would spawn three bots."""
    calls: list = []
    client, repo, row = _seeded_app(calls)

    assert client.post(ENDPOINT, json=goldens["failed-join-evidence"]).status_code == 200
    assert client.post(ENDPOINT, json=goldens["failed-join-evidence"]).status_code == 200
    assert client.post(ENDPOINT, json=goldens["failed-join-evidence"]).status_code == 200

    assert len(calls) == 1


def test_completed_terminal_never_reaches_the_retry_port(goldens):
    calls: list = []
    client, repo, row = _seeded_app(calls)
    repo.set_status(row["id"], "active")

    assert client.post(ENDPOINT, json=goldens["completed-stopped"]).status_code == 200
    assert calls == []


def test_a_throwing_retry_port_never_fails_the_bot_callback(goldens):
    """A retry is an optimization on a run that has already ended. It must never be able to turn a
    healthy terminal callback into an error the bot then retries."""
    repo = InMemoryMeetingRepo()
    row = asyncio.run(repo.create_meeting(
        user_id=1, platform="google_meet", native_meeting_id="m1", data={}))
    asyncio.run(repo.create_session(meeting_id=row["id"], session_uid="sess-uid"))
    repo.set_status(row["id"], "joining")

    async def _boom(meeting_row):
        raise RuntimeError("scheduler exploded")

    client = TestClient(create_app(meeting_repo=repo, join_retry=_boom))
    r = client.post(ENDPOINT, json=goldens["failed-join-evidence"])
    assert r.status_code == 200, r.text
    assert r.json()["meeting_status"] == "failed"
