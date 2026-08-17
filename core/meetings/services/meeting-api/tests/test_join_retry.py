"""P3d eval — join-retry as a control-plane re-spawn (new session).

FakeClock drives N attempts with backoff on a transient join-failure; the loop stops on success,
on the attempt-cap, or on a permanent reason. Each retry is its OWN re-spawn request (a fresh
`meeting_session`); a permanent reason NEVER retries.

OFFLINE — the shipped `JoinRetryController` over the mirrored `Scheduler` + `FakeClock`, with a
capturing dispatch (no real bot spawns, no docker, no network).
"""
from __future__ import annotations

import pytest

from meeting_api.lifecycle import (
    CompletionReason,
    JoinFailureReason,
    JoinRetryController,
    RetryClass,
    RetryPolicy,
    classify_retry,
    evidence_reason,
    is_transient,
    retry_decision,
)
from meeting_api.scheduling import FakeClock, Scheduler

MEETING_ID = 99


# ── the taxonomy ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("reason", [
    CompletionReason.AWAITING_ADMISSION_TIMEOUT,
    CompletionReason.JOIN_FAILURE,
])
def test_transient_reasons(reason):
    assert is_transient(reason)
    assert classify_retry(reason) is RetryClass.TRANSIENT


@pytest.mark.parametrize("reason", [
    CompletionReason.AWAITING_ADMISSION_REJECTED,
    CompletionReason.EVICTED,
    CompletionReason.VALIDATION_ERROR,
    CompletionReason.MAX_BOT_TIME_EXCEEDED,
    CompletionReason.STOPPED,
    CompletionReason.AUTH_SESSION_MISSING,
])
def test_permanent_reasons(reason):
    assert not is_transient(reason)
    assert classify_retry(reason) is RetryClass.PERMANENT


def test_unknown_reason_is_permanent_failsafe():
    assert classify_retry(None) is RetryClass.PERMANENT


# ── harness ─────────────────────────────────────────────────────────────────────────────────────

def _build(max_attempts=3, backoff=(30.0, 120.0, 300.0), retry_admission_timeout=False):
    """A controller wired to a FakeClock-gated Scheduler with a capturing dispatch.

    The dispatch records every fired re-spawn request and mints a NEW session_uid per attempt
    (a fresh `meeting_session`). Returns (controller, scheduler, clock, fired)."""
    clock = FakeClock(start=1000.0)
    fired: list = []

    def dispatch(request):
        # The scheduler fires the re-spawn request — capture it and mint a fresh session.
        attempt = request["body"]["attempt"]
        session_uid = f"sess-{MEETING_ID}-{attempt}"
        fired.append({"request": request, "session_uid": session_uid})
        return {"status": "spawned", "session_uid": session_uid}

    scheduler = Scheduler(dispatch=dispatch, clock=clock)

    def respawn_request(meeting_id, attempt):
        return {
            "method": "POST",
            "url": "http://meeting-api:8080/bots",
            "headers": {"x-user-id": "7"},
            "body": {"meeting_id": meeting_id, "attempt": attempt, "continue_meeting": True},
        }

    controller = JoinRetryController(
        scheduler, respawn_request,
        policy=RetryPolicy(
            max_attempts=max_attempts, backoff=list(backoff),
            retry_admission_timeout=retry_admission_timeout,
        ),
    )
    return controller, scheduler, clock, fired


# ── transient: bounded retries with backoff, each a NEW session ─────────────────────────────────

def test_transient_retries_with_backoff_until_cap():
    """A transient join-failure on every attempt → retries at the backoff schedule, each a fresh
    session, stopping at the attempt cap."""
    controller, scheduler, clock, fired = _build(max_attempts=3, backoff=(30.0, 120.0, 300.0))

    # attempt 0 (the original spawn) just failed transiently → schedule retry #1
    out = controller.on_join_failure(MEETING_ID, CompletionReason.JOIN_FAILURE, attempt=0)
    assert out.action == "scheduled_retry"
    assert out.next_at == 1000.0 + 30.0  # backoff[0] for attempt 1

    # not due yet
    clock.advance(29)
    assert scheduler.tick() == 0
    assert fired == []

    # due → fires retry #1 (a NEW session)
    clock.advance(1)
    assert scheduler.tick() == 1
    assert len(fired) == 1
    assert fired[0]["session_uid"] == f"sess-{MEETING_ID}-1"

    # retry #1 also fails transiently → schedule retry #2 at backoff[1]=120s
    out = controller.on_join_failure(MEETING_ID, CompletionReason.JOIN_FAILURE, attempt=1)
    assert out.action == "scheduled_retry"
    assert out.next_at == clock.now() + 120.0
    clock.advance(120)
    assert scheduler.tick() == 1
    assert fired[1]["session_uid"] == f"sess-{MEETING_ID}-2"

    # retry #2 fails → next_attempt would be 3 == max_attempts → EXHAUSTED (no more retries)
    out = controller.on_join_failure(MEETING_ID, CompletionReason.JOIN_FAILURE, attempt=2)
    assert out.action == "exhausted"

    # each fired retry was its OWN session (distinct session_uids)
    assert [f["session_uid"] for f in fired] == [f"sess-{MEETING_ID}-1", f"sess-{MEETING_ID}-2"]
    assert len({f["session_uid"] for f in fired}) == 2


def test_transient_then_success_stops_retrying():
    """If a retry succeeds, no further retry is scheduled (the caller stops on success)."""
    controller, scheduler, clock, fired = _build(max_attempts=3)
    out = controller.on_join_failure(MEETING_ID, CompletionReason.JOIN_FAILURE, attempt=0)
    assert out.action == "scheduled_retry"
    clock.advance(30)
    scheduler.tick()  # retry #1 fires and (in this scenario) succeeds
    assert len(fired) == 1
    # success → the control plane does NOT call on_join_failure again; no second job is scheduled
    assert scheduler.list(status="pending") == []


# ── permanent: never retries ────────────────────────────────────────────────────────────────────

def test_permanent_reason_never_retries():
    controller, scheduler, clock, fired = _build()
    out = controller.on_join_failure(MEETING_ID, CompletionReason.AWAITING_ADMISSION_REJECTED, attempt=0)
    assert out.action == "permanent"
    assert out.job_id is None
    # advancing time fires nothing — no job was ever scheduled
    clock.advance(10_000)
    assert scheduler.tick() == 0
    assert fired == []


def test_user_stop_is_permanent():
    """A user `stopped` terminal is permanent — a stopped meeting is never auto-respawned."""
    controller, scheduler, clock, fired = _build()
    out = controller.on_join_failure(MEETING_ID, CompletionReason.STOPPED, attempt=0)
    assert out.action == "permanent"
    clock.advance(10_000)
    assert scheduler.tick() == 0


# ── #1190: the decision authority — evidence guard + the admission-timeout arm ──────────────────


def _evidence(reason: JoinFailureReason, **extra) -> dict:
    return {"reason": reason.value, "attribution": "system_fault", "source": "bot", **extra}


def test_redirect_shaped_join_failure_is_never_retried():
    """THE REDIRECT TRAP (#1190). All 56 six-week `teams_auth_redirect` failures land as a sealed
    `join_failure` — TRANSIENT by the taxonomy — and are a hard tenant policy in fact. #1075's typed
    evidence classifies them `navigation_failure`; the decision consults it and refuses."""
    ev = _evidence(JoinFailureReason.NAVIGATION_FAILURE, detail="teams_auth_redirect")
    # the sealed label alone still reads transient — this is exactly why the guard exists
    assert is_transient(CompletionReason.JOIN_FAILURE)
    assert retry_decision(CompletionReason.JOIN_FAILURE, evidence=ev) is RetryClass.PERMANENT

    controller, scheduler, clock, fired = _build()
    out = controller.on_join_failure(
        MEETING_ID, CompletionReason.JOIN_FAILURE, attempt=0, evidence=ev
    )
    assert out.action == "permanent"
    assert out.job_id is None
    clock.advance(10_000)
    assert scheduler.tick() == 0
    assert fired == []


@pytest.mark.parametrize("reason", [
    JoinFailureReason.NAVIGATION_FAILURE,
    JoinFailureReason.PLATFORM_REJECTION,
    JoinFailureReason.AUTH_SESSION_MISSING,
    JoinFailureReason.STOPPED_BEFORE_ADMISSION,
])
def test_permanent_evidence_reasons_override_a_transient_seal(reason):
    assert retry_decision(
        CompletionReason.JOIN_FAILURE, evidence=_evidence(reason)
    ) is RetryClass.PERMANENT


@pytest.mark.parametrize("reason", [
    JoinFailureReason.NEVER_REACHED_LOBBY,   # selector rot — a fresh session can still win
    JoinFailureReason.UNKNOWN,               # the honest "we don't know" population
])
def test_recoverable_evidence_reasons_still_retry(reason):
    assert retry_decision(
        CompletionReason.JOIN_FAILURE, evidence=_evidence(reason)
    ) is RetryClass.TRANSIENT


def test_unusable_evidence_falls_back_to_the_sealed_taxonomy():
    """Evidence is an OVERRIDE, never a precondition: an absent / foreign / newer-vocabulary block
    leaves the decision exactly where it was before #1190."""
    for junk in (None, {}, {"reason": "a_reason_from_a_newer_bot"}, "nonsense", 7):
        assert evidence_reason(junk) in (None, JoinFailureReason.UNKNOWN)
        assert retry_decision(CompletionReason.JOIN_FAILURE, evidence=junk) is RetryClass.TRANSIENT
        assert retry_decision(CompletionReason.EVICTED, evidence=junk) is RetryClass.PERMANENT


def test_admission_timeout_is_not_retried_by_default():
    """Founder ruling 2026-08-17: the bot already stood at the door for the whole lobby budget."""
    controller, scheduler, clock, fired = _build()
    out = controller.on_join_failure(
        MEETING_ID, CompletionReason.AWAITING_ADMISSION_TIMEOUT, attempt=0
    )
    assert out.action == "permanent"
    clock.advance(10_000)
    assert scheduler.tick() == 0
    assert fired == []


def test_admission_timeout_named_only_by_the_evidence_is_also_not_retried():
    """The ~13min `join_failure` whose evidence reads `admission_timeout` is the SAME event under a
    different label — the arm has to close on both axes or the ruling leaks through the seal."""
    controller, scheduler, clock, fired = _build()
    out = controller.on_join_failure(
        MEETING_ID, CompletionReason.JOIN_FAILURE, attempt=0,
        evidence=_evidence(JoinFailureReason.ADMISSION_TIMEOUT),
    )
    assert out.action == "permanent"
    assert scheduler.list(status="pending") == []


def test_admission_timeout_retries_when_the_config_arm_is_on():
    controller, scheduler, clock, fired = _build(retry_admission_timeout=True)
    out = controller.on_join_failure(
        MEETING_ID, CompletionReason.AWAITING_ADMISSION_TIMEOUT, attempt=0
    )
    assert out.action == "scheduled_retry"
    assert out.next_at == clock.now() + 30.0
    clock.advance(30)
    assert scheduler.tick() == 1
    assert len(fired) == 1


def test_each_attempt_is_a_distinct_idempotent_job():
    """Re-scheduling the SAME attempt is idempotent (same idempotency_key → same job)."""
    controller, scheduler, clock, fired = _build()
    a = controller.on_join_failure(MEETING_ID, CompletionReason.JOIN_FAILURE, attempt=0)
    b = controller.on_join_failure(MEETING_ID, CompletionReason.JOIN_FAILURE, attempt=0)
    assert a.job_id == b.job_id  # idempotency_key join_retry:99:1 dedups the duplicate
    # only one pending retry job exists
    assert len(scheduler.list(status="pending")) == 1
