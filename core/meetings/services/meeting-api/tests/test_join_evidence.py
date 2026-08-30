"""L1 — typed join-failure evidence, from classification through to the persisted row (#1059, #1058).

The production defect this file exists to make impossible again: 83 consecutive hosted
``join_failure`` meetings, every one of them carrying ``data.reason = None``, and every one of them
labelled identically whether the platform refused the bot in twenty seconds or nobody answered the
door for thirteen minutes. The system knew; the row did not.

What is proved here, offline (no docker, no DB, no network — TestClient + the in-memory repo):

  1. **Classification.** The Python taxonomy derives the right typed reason from the signals a
     producer actually holds — including the #1058 split, made from the timing signature alone.
  2. **Attribution.** Every typed reason maps to the owner the reliability gate reads, and the two
     evidence-driven overrides fire. This is what makes
     ``system_failure_rate = failures(system_fault) / fair-chance meetings`` computable.
  3. **Persistence.** A terminal callback lands ``data.reason`` AND ``data.join_evidence`` on the
     meeting row — the exact query (``data->>'reason'``) that returned NULL 83 times now answers.
  4. **Surfacing.** The evidence survives the list-view projection (which drops the heavy forensics
     keys) so it is aggregatable from ``GET /meetings``, not just from a single detail fetch.
  5. **Replayable fixtures.** One recorded terminal per failure class, replayed through the shipped
     HTTP callback, deterministically classified.
  6. **Reconcile-driven terminals.** The sweep's OWN failures — the ones the issue traced to a
     branch that set no reason at all — carry evidence too, tagged as second-hand.
  7. **Fail-open.** A hostile or malformed evidence payload degrades the report and nothing else;
     the FSM advance, the terminal status, and the callback's 200 are untouched.
  8. **Cross-language parity.** The bot's TypeScript vocabulary and this one are the same words.

This module owns its own fixtures deliberately (``conftest.py`` is left alone).
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.collector.projection import LIST_OMIT_KEYS, project_list_data
from meeting_api.lifecycle.join_evidence import (
    DETAIL_MAX_CHARS,
    JoinFailureAttribution,
    JoinFailureReason,
    attribute_join_failure,
    build_join_evidence,
    classify_join_failure,
    evidence_from_event,
    normalize_evidence,
)
from meeting_api.lifecycle.machine import LifecycleSink, MeetingStore

ENDPOINT = "/bots/internal/callback/lifecycle"

#: The lobby budget the control plane issues in production (``bot_spawn.service.LOBBY_BUDGET_MS``).
BUDGET_MS = 600_000


# ── local fixtures (this module's own; conftest.py untouched) ─────────────────────────────────────


def _repo_with_meeting(status: str = "requested", session_uid: str = "sess-evidence"):
    """A meeting + session in the in-memory repo, forced to ``status`` (the post-restart shape)."""
    repo = InMemoryMeetingRepo()
    meeting = asyncio.run(
        repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="m1", data={})
    )
    asyncio.run(repo.create_session(meeting_id=meeting["id"], session_uid=session_uid))
    repo.set_status(meeting["id"], status)
    return repo, meeting


def _drive(client: TestClient, *events: dict) -> None:
    """POST a sequence of lifecycle.v1 events through the shipped callback, asserting each lands."""
    for ev in events:
        r = client.post(ENDPOINT, json=ev)
        assert r.status_code == 200, f"{ev.get('status')} rejected: {r.text}"


def _stored_data(repo: InMemoryMeetingRepo, meeting_id: int) -> dict:
    return repo._meetings[meeting_id]["data"]


# ══ 1. classification ════════════════════════════════════════════════════════════════════════════

# (label, kwargs, expected reason). The two #1058 populations lead: they are the whole point.
_CLASSIFY_CASES = [
    (
        "meet: 13min in a 10min lobby budget → awaiting_admission_timeout",
        dict(completion_reason="join_failure", stage="awaiting_admission", reached_lobby=True,
             time_in_lobby_ms=780_000, lobby_budget_ms=BUDGET_MS,
             detail="still in the Google Meet waiting room after timeout"),
        JoinFailureReason.AWAITING_ADMISSION_TIMEOUT,
    ),
    (
        "teams: dead at ~20s having never seen a lobby → never_reached_lobby",
        dict(completion_reason="join_failure", stage="joining", reached_lobby=False,
             detail="Bot failed to join the Teams meeting - no meeting indicators found"),
        JoinFailureReason.NEVER_REACHED_LOBBY,
    ),
    (
        "a lobby exit at 3% of the budget is a refusal, not an expiry",
        dict(completion_reason="join_failure", stage="awaiting_admission", reached_lobby=True,
             time_in_lobby_ms=19_000, lobby_budget_ms=BUDGET_MS, detail="removed from the waiting room"),
        JoinFailureReason.AWAITING_ADMISSION_REJECTED,
    ),
    (
        "exactly 90% of the budget is already an expiry (boundary)",
        dict(completion_reason="join_failure", stage="awaiting_admission", reached_lobby=True,
             time_in_lobby_ms=540_000, lobby_budget_ms=BUDGET_MS),
        JoinFailureReason.AWAITING_ADMISSION_TIMEOUT,
    ),
    (
        "one ms under the boundary is still a refusal",
        dict(completion_reason="join_failure", stage="awaiting_admission", reached_lobby=True,
             time_in_lobby_ms=539_999, lobby_budget_ms=BUDGET_MS),
        JoinFailureReason.AWAITING_ADMISSION_REJECTED,
    ),
    (
        "the sealed rejection reason is read directly",
        dict(completion_reason="awaiting_admission_rejected", stage="awaiting_admission"),
        JoinFailureReason.AWAITING_ADMISSION_REJECTED,
    ),
    (
        "the sealed timeout reason is read directly",
        dict(completion_reason="awaiting_admission_timeout", stage="awaiting_admission"),
        JoinFailureReason.AWAITING_ADMISSION_TIMEOUT,
    ),
    (
        "the sealed auth reason is read directly",
        dict(completion_reason="auth_session_missing", stage="joining"),
        JoinFailureReason.AUTH_SESSION_MISSING,
    ),
    (
        "a pre-active `stopped` is the USER, not a defect",
        dict(completion_reason="stopped", stage="awaiting_admission", reached_lobby=True),
        JoinFailureReason.STOPPED_WHILE_JOINING,
    ),
    (
        "a transport marker names a navigation failure even from inside the lobby",
        dict(completion_reason="join_failure", stage="awaiting_admission", reached_lobby=True,
             time_in_lobby_ms=4_000, lobby_budget_ms=BUDGET_MS,
             detail="page.goto: net::ERR_NAME_NOT_RESOLVED"),
        JoinFailureReason.NAVIGATION_FAILURE,
    ),
    (
        "the Teams sign-in redirect is a navigation failure",
        dict(completion_reason="join_failure", stage="joining",
             detail="teams_auth_redirect: https://login.microsoftonline.com/..."),
        JoinFailureReason.NAVIGATION_FAILURE,
    ),
    (
        "a control plane down at boot: we never navigated at all",
        dict(completion_reason="join_failure", stage="requested",
             detail="control_plane_unreachable: refused to join"),
        JoinFailureReason.NAVIGATION_FAILURE,
    ),
    (
        "reaped IN the lobby with no timings at all: the stage is still evidence",
        dict(completion_reason="join_failure", stage="awaiting_admission"),
        JoinFailureReason.AWAITING_ADMISSION_TIMEOUT,
    ),
    (
        "reaped at `requested` — the bot never spoke, so we honestly do not know",
        dict(completion_reason="join_failure", stage="requested",
             detail="workload c-1 state=exited exitCode=137"),
        JoinFailureReason.UNKNOWN,
    ),
    (
        "no signals whatsoever → unknown, never a guess",
        dict(),
        JoinFailureReason.UNKNOWN,
    ),
]


@pytest.mark.parametrize("label,kwargs,expected", _CLASSIFY_CASES, ids=[c[0] for c in _CLASSIFY_CASES])
def test_classification(label, kwargs, expected):
    assert classify_join_failure(**kwargs) is expected


def test_the_two_conflated_populations_are_now_different_reasons():
    """#1058, stated as its own assertion: the ~20s Teams death and the ~13min Meet death arrive
    with the SAME sealed ``completion_reason`` and must leave with different typed reasons."""
    teams = classify_join_failure(
        completion_reason="join_failure", stage="joining", reached_lobby=False,
        time_in_lobby_ms=None, detail="no meeting indicators found after polling",
    )
    meet = classify_join_failure(
        completion_reason="join_failure", stage="awaiting_admission", reached_lobby=True,
        time_in_lobby_ms=793_000, lobby_budget_ms=BUDGET_MS, detail="host did not admit",
    )
    assert teams is not meet
    assert (teams, meet) == (JoinFailureReason.NEVER_REACHED_LOBBY, JoinFailureReason.AWAITING_ADMISSION_TIMEOUT)


# ══ 2. attribution ═══════════════════════════════════════════════════════════════════════════════

_ATTRIBUTION_CASES = [
    (JoinFailureReason.AWAITING_ADMISSION_REJECTED, JoinFailureAttribution.HOST_ACTION),
    (JoinFailureReason.AWAITING_ADMISSION_TIMEOUT, JoinFailureAttribution.HOST_ACTION),
    (JoinFailureReason.AUTH_SESSION_MISSING, JoinFailureAttribution.SYSTEM_FAULT),
    (JoinFailureReason.NEVER_REACHED_LOBBY, JoinFailureAttribution.SYSTEM_FAULT),
    (JoinFailureReason.NAVIGATION_FAILURE, JoinFailureAttribution.SYSTEM_FAULT),
    (JoinFailureReason.STOPPED_WHILE_JOINING, JoinFailureAttribution.USER_ACTION),
    (JoinFailureReason.UNKNOWN, JoinFailureAttribution.UNKNOWN),
]


@pytest.mark.parametrize("reason,expected", _ATTRIBUTION_CASES, ids=[c[0].value for c in _ATTRIBUTION_CASES])
def test_attribution_default_per_reason(reason, expected):
    assert attribute_join_failure(reason) is expected


def test_every_reason_has_an_attribution():
    """No typed reason may fall through to a missing owner — a row the gate metric cannot bucket is
    a row the gate metric silently drops."""
    for reason in JoinFailureReason:
        assert isinstance(attribute_join_failure(reason), JoinFailureAttribution)


def test_platform_policy_refusal_is_exogenous_not_the_hosts_doing():
    assert attribute_join_failure(
        JoinFailureReason.AWAITING_ADMISSION_REJECTED,
        detail="[Zoom Web] zoom_requires_rtms: meeting/account blocks automated browser joins",
    ) is JoinFailureAttribution.EXOGENOUS_PLATFORM
    assert attribute_join_failure(
        JoinFailureReason.AWAITING_ADMISSION_REJECTED, detail="reCAPTCHA challenge presented",
    ) is JoinFailureAttribution.EXOGENOUS_PLATFORM


def test_a_broken_admission_flow_is_ours_not_the_hosts():
    """The founder's carve-out: a host who never admitted is ``host_action`` UNLESS the evidence
    shows the admission flow itself broke — then the host never got the chance to decline."""
    assert attribute_join_failure(
        JoinFailureReason.AWAITING_ADMISSION_TIMEOUT, detail="no meeting indicators found after polling",
    ) is JoinFailureAttribution.SYSTEM_FAULT
    # …and without that evidence it stays with the host.
    assert attribute_join_failure(
        JoinFailureReason.AWAITING_ADMISSION_TIMEOUT, detail="host did not admit within the waiting-room timeout",
    ) is JoinFailureAttribution.HOST_ACTION


def test_overrides_are_scoped_to_their_reason():
    """An override must not leak across reasons — admission-flow words do not move a rejection, and
    a user stop is never reattributed however alarming its detail looks."""
    assert attribute_join_failure(
        JoinFailureReason.AWAITING_ADMISSION_REJECTED, detail="no meeting indicators found",
    ) is JoinFailureAttribution.HOST_ACTION
    assert attribute_join_failure(
        JoinFailureReason.STOPPED_WHILE_JOINING, detail="net::ERR_FAILED; no meeting indicators found",
    ) is JoinFailureAttribution.USER_ACTION


def test_the_gate_metric_separates_the_two_populations():
    """The reason the attribution axis exists: of #1058's two populations, exactly one is ours."""
    meet = build_join_evidence(
        reason=JoinFailureReason.AWAITING_ADMISSION_TIMEOUT, stage="awaiting_admission",
        detail="host did not admit", time_in_lobby_ms=780_000, lobby_budget_ms=BUDGET_MS,
    )
    teams = build_join_evidence(
        reason=JoinFailureReason.NEVER_REACHED_LOBBY, stage="joining",
        detail="no meeting indicators found", total_ms=19_600,
    )
    assert meet["attribution"] == JoinFailureAttribution.HOST_ACTION.value
    assert teams["attribution"] == JoinFailureAttribution.SYSTEM_FAULT.value


# ══ 3. the evidence block ════════════════════════════════════════════════════════════════════════


def test_build_emits_only_what_is_known():
    ev = build_join_evidence(
        reason=JoinFailureReason.AWAITING_ADMISSION_TIMEOUT, stage="awaiting_admission", source="bot",
        detail="host did not admit", time_to_lobby_ms=7_000, time_in_lobby_ms=780_000,
        total_ms=787_000, lobby_budget_ms=BUDGET_MS,
    )
    assert ev["reason"] == "awaiting_admission_timeout"
    assert ev["attribution"] == "host_action"
    assert ev["stage"] == "awaiting_admission"
    assert ev["source"] == "bot"
    assert ev["timings"] == {
        "time_to_lobby_ms": 7_000, "time_in_lobby_ms": 780_000, "total_ms": 787_000,
    }
    assert ev["lobby_budget_ms"] == BUDGET_MS


def test_an_unmeasured_timing_is_absent_never_a_fabricated_zero():
    ev = build_join_evidence(reason=JoinFailureReason.NEVER_REACHED_LOBBY, stage="joining")
    assert "timings" not in ev
    assert "lobby_budget_ms" not in ev
    assert "detail" not in ev


def test_a_partial_timing_set_keeps_only_the_measured_keys():
    ev = build_join_evidence(reason=JoinFailureReason.NEVER_REACHED_LOBBY, total_ms=19_600)
    assert ev["timings"] == {"total_ms": 19_600}


def test_oversized_platform_signal_is_capped_not_dropped():
    ev = build_join_evidence(reason=JoinFailureReason.UNKNOWN, detail="E" * 5_000)
    assert len(ev["detail"]) == DETAIL_MAX_CHARS
    assert ev["detail"].endswith("…")


@pytest.mark.parametrize("bogus", [None, "not-a-dict", 42, [], {"reason": None}, {"timings": "nope"}])
def test_normalize_never_raises_on_garbage(bogus):
    out = normalize_evidence(bogus)
    assert out is None or isinstance(out, dict)


def test_normalize_preserves_an_unknown_vocabulary_word():
    """A NEWER bot's reason must not vanish: it degrades to ``unknown`` with the word kept, so the
    taxonomy can be extended from the bot side without the control plane silently discarding it."""
    out = normalize_evidence({"reason": "future_reason_we_do_not_know", "detail": "something happened"})
    assert out["reason"] == "unknown"
    assert "future_reason_we_do_not_know" in out["detail"]


def test_normalize_discards_an_uninterpretable_attribution():
    """Attribution feeds the reliability gate — a value we cannot interpret must not be counted as
    anything, so the derivation from the reason takes over."""
    out = normalize_evidence({"reason": "awaiting_admission_timeout", "attribution": "vibes"})
    assert out["attribution"] == "host_action"


def test_normalize_honours_a_valid_producer_attribution():
    out = normalize_evidence({"reason": "awaiting_admission_timeout", "attribution": "system_fault",
                              "detail": "our admission flow wedged"})
    assert out["attribution"] == "system_fault"


def test_normalize_rejects_impossible_timings():
    out = normalize_evidence({
        "reason": "awaiting_admission_timeout",
        "timings": {"time_in_lobby_ms": -1, "total_ms": "soon", "time_to_lobby_ms": True},
    })
    assert "timings" not in out


# ══ 4. persistence — the #1059 regression ════════════════════════════════════════════════════════


def test_reason_lands_at_the_top_level_of_meeting_data():
    """THE #1059 REGRESSION. ``SELECT data->>'reason'`` returned NULL for all 83 production rows
    because the reason was only ever projected into ``last_error.reason``. It is now a top-level key.
    """
    repo, meeting = _repo_with_meeting()
    client = TestClient(create_app(meeting_repo=repo))
    _drive(
        client,
        {"connection_id": "sess-evidence", "status": "joining"},
        {"connection_id": "sess-evidence", "status": "failed", "exit_code": 1,
         "completion_reason": "join_failure",
         "reason": "Bot failed to join the Teams meeting - no meeting indicators found"},
    )
    data = _stored_data(repo, meeting["id"])
    assert data["reason"] == "Bot failed to join the Teams meeting - no meeting indicators found"
    assert data["reason"] is not None  # the literal shape of the production query that returned None


def test_join_evidence_lands_on_the_meeting_row():
    repo, meeting = _repo_with_meeting()
    client = TestClient(create_app(meeting_repo=repo))
    _drive(
        client,
        {"connection_id": "sess-evidence", "status": "joining"},
        {"connection_id": "sess-evidence", "status": "awaiting_admission"},
        {"connection_id": "sess-evidence", "status": "failed", "exit_code": 1,
         "completion_reason": "awaiting_admission_timeout",
         "reason": "host did not admit",
         "join_evidence": {
             "reason": "awaiting_admission_timeout", "attribution": "host_action", "source": "bot",
             "stage": "awaiting_admission", "detail": "host did not admit",
             "timings": {"time_to_lobby_ms": 7_000, "time_in_lobby_ms": 780_000, "total_ms": 787_000},
             "lobby_budget_ms": BUDGET_MS,
         }},
    )
    ev = _stored_data(repo, meeting["id"])["join_evidence"]
    assert ev["reason"] == "awaiting_admission_timeout"
    assert ev["attribution"] == "host_action"
    assert ev["source"] == "bot"
    assert ev["timings"]["time_in_lobby_ms"] == 780_000
    assert ev["lobby_budget_ms"] == BUDGET_MS


def test_evidence_is_derived_when_the_bot_sends_none():
    """A bot too old to classify (or any producer that omits the block) must still leave an
    evidenced row — otherwise the instrument has a hole exactly where the legacy fleet is."""
    repo, meeting = _repo_with_meeting()
    client = TestClient(create_app(meeting_repo=repo))
    _drive(
        client,
        {"connection_id": "sess-evidence", "status": "joining"},
        {"connection_id": "sess-evidence", "status": "awaiting_admission"},
        {"connection_id": "sess-evidence", "status": "failed", "exit_code": 1,
         "completion_reason": "join_failure", "reason": "gave up waiting"},
    )
    ev = _stored_data(repo, meeting["id"])["join_evidence"]
    assert ev["reason"] == "awaiting_admission_timeout"      # derived from the stage it died in
    assert ev["attribution"] == "host_action"
    assert ev["source"] == "derived"                # honestly labelled as second-hand
    assert ev["detail"] == "gave up waiting"


def test_reached_lobby_is_derived_from_history_not_the_payload():
    """FM-003 discipline, applied to the taxonomy: the orchestrator historically stamped
    ``failure_stage: awaiting_admission`` on EVERY non-admitted verdict, including bots that never
    saw a lobby. The record's own history decides, so a stale payload cannot launder a
    never-reached-lobby into an admission timeout."""
    repo, meeting = _repo_with_meeting()
    client = TestClient(create_app(meeting_repo=repo))
    _drive(
        client,
        {"connection_id": "sess-evidence", "status": "joining"},
        {"connection_id": "sess-evidence", "status": "failed", "exit_code": 1,
         "failure_stage": "awaiting_admission",   # the STALE payload claim
         "completion_reason": "join_failure", "reason": "no meeting indicators found"},
    )
    data = _stored_data(repo, meeting["id"])
    assert data["failure_stage"] == "joining"                     # server-derived
    assert data["join_evidence"]["reason"] == "never_reached_lobby"
    assert data["join_evidence"]["attribution"] == "system_fault"


def test_needs_help_is_only_proof_of_a_lobby_when_it_was_entered_from_one():
    """``needs_help`` implies a waiting room only while the FSM can be reached from nowhere else.

    #1251 adds ``joining -> needs_help`` so a PRE-lobby blocker (a consent gate, a captcha) can
    escalate. Reading the mere presence of ``needs_help`` as proof of a lobby would then stamp a bot
    that never saw a waiting room as ``awaiting_admission_timeout`` / ``host_action`` — filed as *the host
    did not let us in*, and excluded from ``system_failure_rate``: the metric under-reporting our own
    defects in exactly the cohort #1251 exists to investigate. Driven at the function, because
    ``LEGAL_TRANSITIONS`` still forbids the edge that makes it reachable.
    """
    from meeting_api.lifecycle.machine import (
        BotStatus,
        FailureStage,
        MeetingRecord,
        _capture_join_evidence,
    )

    event = {"completion_reason": "join_failure", "reason": "blocked before the waiting room"}

    pre_lobby = MeetingRecord(connection_id="sess-blocked")
    pre_lobby.history = [BotStatus.JOINING, BotStatus.NEEDS_HELP]   # the post-#1251 path
    pre_lobby.failure_stage = FailureStage.JOINING
    ev = _capture_join_evidence(pre_lobby, event, BotStatus.NEEDS_HELP)
    assert ev["reason"] == "never_reached_lobby"
    assert ev["attribution"] == "system_fault"

    from_lobby = MeetingRecord(connection_id="sess-lobby")
    from_lobby.history = [BotStatus.JOINING, BotStatus.AWAITING_ADMISSION, BotStatus.NEEDS_HELP]
    from_lobby.failure_stage = FailureStage.AWAITING_ADMISSION
    ev = _capture_join_evidence(from_lobby, event, BotStatus.NEEDS_HELP)
    assert ev["reason"] == "awaiting_admission_timeout"
    assert ev["attribution"] == "host_action"


def test_no_join_evidence_for_a_failure_after_admission():
    """A bot that reached ``active`` was admitted; whatever killed it later is not a join failure.
    The taxonomy stays silent rather than filing it under ``unknown``."""
    repo, meeting = _repo_with_meeting()
    client = TestClient(create_app(meeting_repo=repo))
    _drive(
        client,
        {"connection_id": "sess-evidence", "status": "joining"},
        {"connection_id": "sess-evidence", "status": "active"},
        {"connection_id": "sess-evidence", "status": "failed", "exit_code": 1,
         "completion_reason": "join_failure", "reason": "pipeline start failed"},
    )
    data = _stored_data(repo, meeting["id"])
    assert data["failure_stage"] == "active"
    assert "join_evidence" not in data
    assert data["reason"] == "pipeline start failed"   # the reason still lands


# ══ 5. surfacing — the evidence must survive the projections ═════════════════════════════════════


def test_evidence_survives_the_list_view_projection():
    """The list view drops the heavy forensics keys (``last_error`` among them — which is exactly
    where the reason used to be buried). ``reason`` and ``join_evidence`` are small and must ride
    along, or the failure stays unaggregatable from the list endpoint."""
    assert "reason" not in LIST_OMIT_KEYS
    assert "join_evidence" not in LIST_OMIT_KEYS
    projected = project_list_data({
        "reason": "host did not admit",
        "join_evidence": {"reason": "awaiting_admission_timeout", "attribution": "host_action"},
        "last_error": {"trace": "x" * 5000},
        "bot_logs": ["noise"] * 100,
    })
    assert projected["reason"] == "host did not admit"
    assert projected["join_evidence"]["attribution"] == "host_action"
    assert "last_error" not in projected and "bot_logs" not in projected


@pytest.mark.parametrize("endpoint", ["/meetings", "/bots"])
def test_evidence_surfaces_on_the_list_endpoints(endpoint):
    """The list endpoints are where an operator (or a metric job) reads failures in bulk. They apply
    the heavy-key projection that hides ``last_error`` — the very place the reason used to live — so
    the evidence has to survive it or the instrument is unreadable at scale."""
    from meeting_api.collector.fakes import InMemoryTranscriptStore

    store = InMemoryTranscriptStore()
    store.seed_meeting(
        user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij", status="failed",
        data={
            "completion_reason": "join_failure",
            "failure_stage": "joining",
            "reason": "page.goto: net::ERR_NAME_NOT_RESOLVED",
            "join_evidence": {
                "reason": "navigation_failure", "attribution": "system_fault", "source": "bot",
                "stage": "joining", "detail": "page.goto: net::ERR_NAME_NOT_RESOLVED",
                "timings": {"total_ms": 3_200},
            },
            # the heavy keys the list must still drop
            "last_error": {"trace": "x" * 5000},
            "bot_logs": ["noise"] * 100,
        },
    )
    client = TestClient(create_app(transcript_store=store, meeting_repo=InMemoryMeetingRepo()))
    r = client.get(endpoint, headers={"x-user-id": "7"})
    assert r.status_code == 200, r.text
    row = r.json()["meetings"][0]
    assert row["data"]["reason"] == "page.goto: net::ERR_NAME_NOT_RESOLVED"
    assert row["data"]["join_evidence"]["reason"] == "navigation_failure"
    assert row["data"]["join_evidence"]["attribution"] == "system_fault"
    assert "last_error" not in row["data"] and "bot_logs" not in row["data"]


def test_evidence_rides_the_callback_response_body():
    """The callback echoes ``data`` — the seam a bot (or an operator curling it) reads back."""
    sink = LifecycleSink(store=MeetingStore())
    sink.apply({"connection_id": "c", "status": "joining"})
    rec = sink.apply({
        "connection_id": "c", "status": "failed", "exit_code": 1,
        "completion_reason": "join_failure", "reason": "no meeting indicators found",
    })
    assert rec.data["reason"] == "no meeting indicators found"
    assert rec.data["join_evidence"]["reason"] == "never_reached_lobby"


# ══ 6. replayable fixtures — one recorded terminal per failure class ═════════════════════════════

# Each row is a TAPE: the intermediate states the bot reported, then the exact terminal event it
# emitted. Replaying one through the shipped callback must land the same classification every time —
# a regression tape that needs no browser, no meeting and no platform.
_FIXTURES = [
    (
        "meet-admission-timeout",
        ["joining", "awaiting_admission"],
        {"status": "failed", "exit_code": 1, "completion_reason": "awaiting_admission_timeout",
         "reason": "Bot is still in the Google Meet waiting room after timeout — host did not admit",
         "join_evidence": {
             "reason": "awaiting_admission_timeout", "attribution": "host_action", "source": "bot",
             "stage": "awaiting_admission",
             "detail": "Bot is still in the Google Meet waiting room after timeout — host did not admit",
             "timings": {"time_to_lobby_ms": 7_000, "time_in_lobby_ms": 780_000, "total_ms": 787_000},
             "lobby_budget_ms": BUDGET_MS}},
        ("awaiting_admission_timeout", "host_action"),
    ),
    (
        "teams-never-reached-lobby",
        ["joining"],
        {"status": "failed", "exit_code": 1, "completion_reason": "join_failure",
         "reason": "Bot failed to join the Teams meeting - no meeting indicators found after polling",
         "join_evidence": {
             "reason": "never_reached_lobby", "attribution": "system_fault", "source": "bot",
             "stage": "joining",
             "detail": "Bot failed to join the Teams meeting - no meeting indicators found after polling",
             "timings": {"total_ms": 19_600}}},
        ("never_reached_lobby", "system_fault"),
    ),
    (
        "host-denied-admission",
        ["joining", "awaiting_admission"],
        {"status": "failed", "exit_code": 1, "completion_reason": "awaiting_admission_rejected",
         "reason": "Bot admission was rejected by meeting admin"},
        ("awaiting_admission_rejected", "host_action"),
    ),
    (
        "zoom-blocks-automated-joins",
        ["joining"],
        {"status": "failed", "exit_code": 1, "completion_reason": "awaiting_admission_rejected",
         "reason": "[Zoom Web] zoom_requires_rtms: meeting/account blocks automated browser joins"},
        ("awaiting_admission_rejected", "exogenous_platform"),
    ),
    (
        "signed-out-profile",
        ["joining"],
        {"status": "failed", "exit_code": 1, "completion_reason": "auth_session_missing",
         "reason": "Browser profile signed out — cannot authenticate with Google."},
        ("auth_session_missing", "system_fault"),
    ),
    (
        "navigation-never-landed",
        ["joining"],
        {"status": "failed", "exit_code": 1, "completion_reason": "join_failure",
         "reason": "page.goto: net::ERR_NAME_NOT_RESOLVED at https://meet.google.com/abc"},
        ("navigation_failure", "system_fault"),
    ),
    (
        "user-stopped-in-the-lobby",
        ["joining", "awaiting_admission"],
        {"status": "failed", "exit_code": 0, "completion_reason": "stopped",
         "reason": "stopped while awaiting admission (withdrew the join request)"},
        ("stopped_while_joining", "user_action"),
    ),
]

# The control-plane-unreachable abort (#530) has no HTTP fixture ON PURPOSE: that terminal is emitted
# precisely when the callback channel is down, so it can never be replayed through the callback. Its
# classification is pinned at the unit level instead (see the `_CLASSIFY_CASES` row), and end to end
# on the bot side (`services/bot/src/join-evidence.test.ts`), where the event is actually built.


def test_control_plane_unreachable_abort_is_a_system_fault():
    """The one failure class with no callback fixture — pinned here so it is not simply unmeasured.
    The bot refused to join because OUR control plane was down; it never navigated at all."""
    reason = classify_join_failure(
        completion_reason="join_failure", stage="requested", reached_lobby=False,
        detail="control_plane_unreachable: control plane unreachable at boot "
               "(meeting_api_callback, redis); refused to join",
    )
    assert reason is JoinFailureReason.NAVIGATION_FAILURE
    assert attribute_join_failure(reason) is JoinFailureAttribution.SYSTEM_FAULT


@pytest.mark.parametrize(
    "name,prefix,terminal,expected", _FIXTURES, ids=[f[0] for f in _FIXTURES]
)
def test_failure_class_fixture_replays_deterministically(name, prefix, terminal, expected):
    expected_reason, expected_attribution = expected
    repo, meeting = _repo_with_meeting()
    client = TestClient(create_app(meeting_repo=repo))
    events = [{"connection_id": "sess-evidence", "status": s} for s in prefix]
    # A bot that never emitted `joining` (the control-plane-unreachable abort) still has to land its
    # terminal — the FSM's entry edge is None → joining, so this exercises the rehydrate path too.
    events.append({"connection_id": "sess-evidence", **terminal})
    for ev in events:
        r = client.post(ENDPOINT, json=ev)
        assert r.status_code in (200, 409), r.text
    data = _stored_data(repo, meeting["id"])
    assert data.get("reason"), f"[{name}] the terminal landed with no reason at all (#1059)"
    ev = data.get("join_evidence")
    assert ev is not None, f"[{name}] no join_evidence persisted"
    assert ev["reason"] == expected_reason, f"[{name}] got {ev['reason']}"
    assert ev["attribution"] == expected_attribution, f"[{name}] got {ev['attribution']}"


def test_fixtures_cover_every_typed_reason_and_attribution():
    """The tape library must exercise the whole vocabulary — a value with no fixture is a value
    nobody has ever seen produced."""
    reasons = {f[3][0] for f in _FIXTURES}
    attributions = {f[3][1] for f in _FIXTURES}
    # `navigation_failure` also has the control-plane variant pinned separately (above); `unknown` is
    # covered by the sweep's `requested` case and the hostile-payload matrix, not by a tape — there is
    # no such thing as a recording of a failure nobody observed.
    assert reasons == {r.value for r in JoinFailureReason} - {"unknown"}
    assert attributions == {a.value for a in JoinFailureAttribution} - {"unknown"}


# ══ 7. reconcile-driven terminals ════════════════════════════════════════════════════════════════

from meeting_api.lifecycle.reconcile import (  # noqa: E402
    reconcile_stale_nonterminal_sweep,
    synthesize_terminal_for_dead_workload,
)


def _run_sweep(client: TestClient, repo: InMemoryMeetingRepo):
    import logging

    async def _post(body: dict):
        return client.post(ENDPOINT, json=body).status_code

    return asyncio.run(reconcile_stale_nonterminal_sweep(
        repo, None, _post, stop_grace=45.0, active_grace=300.0,
        log=logging.getLogger("test.reconcile"),
    ))


@pytest.mark.parametrize(
    "status,expected_reason,expected_attribution",
    [
        ("awaiting_admission", "awaiting_admission_timeout", "host_action"),
        ("joining", "never_reached_lobby", "system_fault"),
    ],
)
def test_reconcile_sweep_stamps_typed_evidence(status, expected_reason, expected_attribution):
    """The sweep's own pre-active terminals — the branch #1059 traced as setting no reason — now
    carry the typed axes. It holds no timings (it never saw the platform), so it emits none and
    labels itself ``reconcile`` rather than inventing first-hand measurements."""
    repo, meeting = _repo_with_meeting(status=status)
    client = TestClient(create_app(meeting_repo=repo))

    assert _run_sweep(client, repo) == 1

    data = _stored_data(repo, meeting["id"])
    assert data["completion_reason"] in ("join_failure", "awaiting_admission_timeout")
    assert data["reason"], "the sweep's evidence note must reach data.reason (#1059)"
    ev = data["join_evidence"]
    assert ev["reason"] == expected_reason
    assert ev["attribution"] == expected_attribution
    assert ev["source"] == "reconcile"
    assert "timings" not in ev, "the sweep must not invent timings it never measured"


def test_a_row_stuck_at_requested_still_cannot_be_reconciled_at_all():
    """A PRE-EXISTING gap this instrument makes visible rather than fixes, pinned so it is not
    mistaken for a taxonomy bug.

    A meeting still at ``requested`` (the bot never emitted a single ``joining``) cannot be advanced
    through the lifecycle callback at all: the FSM's only legal first edge is ``None → joining``, so
    the sweep's synthetic ``failed`` is rejected 409 and the row keeps its status. It therefore gets
    no evidence — not because the taxonomy has nothing to say (it would honestly say ``unknown``),
    but because the terminal never lands. Fixing that is an FSM-entry change, out of scope here and
    filed separately."""
    repo, meeting = _repo_with_meeting(status="requested")
    client = TestClient(create_app(meeting_repo=repo))

    _run_sweep(client, repo)

    assert repo._meetings[meeting["id"]]["status"] == "requested"
    assert "join_evidence" not in _stored_data(repo, meeting["id"])


def test_reconcile_sweep_reason_carries_the_probes_own_answer():
    """ATTRIBUTE, never manufacture: the persisted detail is the note the sweep actually wrote."""
    repo, meeting = _repo_with_meeting(status="awaiting_admission")
    client = TestClient(create_app(meeting_repo=repo))
    _run_sweep(client, repo)
    data = _stored_data(repo, meeting["id"])
    assert "reconciled to failed at awaiting_admission" in data["reason"]
    assert data["join_evidence"]["detail"] == data["reason"]


def test_runtime_destroy_terminal_stamps_typed_evidence():
    """The other synthetic-terminal source (a runtime-confirmed workload destroy) is evidenced too,
    and says so — ``source=runtime_destroy``."""
    import logging

    repo, meeting = _repo_with_meeting(status="awaiting_admission")
    asyncio.run(repo.set_bot_container(meeting_id=meeting["id"], bot_container_id="wl-1"))
    client = TestClient(create_app(meeting_repo=repo))

    async def _drive_terminal(body: dict):
        return client.post(ENDPOINT, json=body).status_code

    drove = asyncio.run(synthesize_terminal_for_dead_workload(
        repo, "wl-1", "exited", _drive_terminal, log=logging.getLogger("test.runtime"),
    ))
    assert drove is True
    ev = _stored_data(repo, meeting["id"])["join_evidence"]
    assert ev["reason"] == "awaiting_admission_timeout"
    assert ev["attribution"] == "host_action"
    assert ev["source"] == "runtime_destroy"


# ══ 8. fail-open ═════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("hostile", [
    {"join_evidence": "not-an-object"},
    {"join_evidence": {"reason": {"nested": "junk"}, "timings": ["nope"]}},
    {"join_evidence": {"reason": "awaiting_admission_timeout", "timings": {"total_ms": "NaN"}}},
    {"join_evidence": {"reason": 7, "attribution": ["system_fault"], "detail": {"a": 1}}},
    {"join_evidence": None},
    {"join_evidence": []},
])
def test_a_hostile_evidence_payload_never_breaks_the_terminal(hostile):
    """FAIL-OPEN, the load-bearing guarantee: a producer can send anything under ``join_evidence``
    and the meeting still reaches the SAME terminal state, with the SAME 200 at the callback. Only
    the report degrades."""
    repo, meeting = _repo_with_meeting()
    client = TestClient(create_app(meeting_repo=repo))
    _drive(client, {"connection_id": "sess-evidence", "status": "joining"})
    r = client.post(ENDPOINT, json={
        "connection_id": "sess-evidence", "status": "failed", "exit_code": 1,
        "completion_reason": "join_failure", "reason": "something went wrong", **hostile,
    })
    assert r.status_code == 200, r.text
    data = _stored_data(repo, meeting["id"])
    assert repo._meetings[meeting["id"]]["status"] == "failed"
    assert data["completion_reason"] == "join_failure"
    assert data["reason"] == "something went wrong"
    ev = data.get("join_evidence")
    assert ev is None or (ev["reason"] in {r.value for r in JoinFailureReason}
                          and ev["attribution"] in {a.value for a in JoinFailureAttribution})


def test_classification_is_total_under_hostile_input():
    """Every entry point must be total. A non-string detail, an unknown stage, an unorderable
    timing — none of these may raise into the join path."""
    assert classify_join_failure(detail=object(), stage=object()) is JoinFailureReason.UNKNOWN  # type: ignore[arg-type]
    assert classify_join_failure(
        stage="awaiting_admission", reached_lobby=True, time_in_lobby_ms="soon",  # type: ignore[arg-type]
        lobby_budget_ms=BUDGET_MS,
    ) is JoinFailureReason.AWAITING_ADMISSION_TIMEOUT
    assert evidence_from_event({}) is not None
    assert attribute_join_failure("not-a-reason") is JoinFailureAttribution.UNKNOWN  # type: ignore[arg-type]


def test_evidence_capture_survives_a_broken_taxonomy(monkeypatch):
    """Belt and braces: even if the classifier itself blows up, the FSM advance must be unaffected —
    the meeting still fails, still persists its reason, and the callback still answers 200."""
    import meeting_api.lifecycle.join_evidence as je

    def _boom(*a, **k):
        raise RuntimeError("taxonomy exploded")

    monkeypatch.setattr(je, "evidence_from_event", _boom)
    repo, meeting = _repo_with_meeting()
    client = TestClient(create_app(meeting_repo=repo))
    _drive(client, {"connection_id": "sess-evidence", "status": "joining"})
    r = client.post(ENDPOINT, json={
        "connection_id": "sess-evidence", "status": "failed", "exit_code": 1,
        "completion_reason": "join_failure", "reason": "the run failed normally",
    })
    assert r.status_code == 200, r.text
    data = _stored_data(repo, meeting["id"])
    assert repo._meetings[meeting["id"]]["status"] == "failed"
    assert data["reason"] == "the run failed normally"
    assert "join_evidence" not in data


# ══ 9. cross-language parity ═════════════════════════════════════════════════════════════════════


def _bot_union_members(source: str, type_name: str) -> set[str]:
    """Extract the string-literal members of a TS union type declaration."""
    # Match up to the union's LAST member (`'value';`) rather than the first `;` — the JSDoc
    # comments between members contain prose semicolons.
    m = re.search(rf"export type {type_name} =(.*?';)", source, re.S)
    assert m, f"{type_name} not found in the bot taxonomy"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_the_bot_and_control_plane_speak_the_same_vocabulary():
    """The bot classifies at the source and the control plane re-derives when it must. Two
    implementations of one taxonomy drift silently unless something checks — this is that check."""
    root = Path(__file__).resolve()
    ts = None
    for parent in root.parents:
        candidate = parent / "meetings" / "services" / "bot" / "src" / "join-evidence.ts"
        if candidate.is_file():
            ts = candidate.read_text()
            break
    assert ts is not None, "bot join-evidence.ts not found from the meeting-api tests"

    assert _bot_union_members(ts, "JoinFailureReason") == {r.value for r in JoinFailureReason}
    assert _bot_union_members(ts, "JoinFailureAttribution") == {a.value for a in JoinFailureAttribution}


def test_the_two_implementations_agree_on_the_lobby_expiry_boundary():
    """The single most consequential shared constant: it is what splits #1058's two populations."""
    root = Path(__file__).resolve()
    for parent in root.parents:
        candidate = parent / "meetings" / "services" / "bot" / "src" / "join-evidence.ts"
        if candidate.is_file():
            ts = candidate.read_text()
            break
    else:  # pragma: no cover - the previous test already asserts the file is reachable
        pytest.skip("bot taxonomy not reachable")

    from meeting_api.lifecycle.join_evidence import LOBBY_EXPIRY_FRACTION

    assert f"LOBBY_EXPIRY_FRACTION = {LOBBY_EXPIRY_FRACTION}" in ts
    assert f"DETAIL_MAX_CHARS = {DETAIL_MAX_CHARS}" in ts
