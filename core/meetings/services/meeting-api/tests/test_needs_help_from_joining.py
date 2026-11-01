"""`joining -> needs_help`: a pre-lobby escalation must land, keep its reason, and stay truthfully ours.

Prod, 24 h to 28 Oct 2026: 9 of 41 Google Meet failures went `joining -> failed` after ~15 min and
never reported `awaiting_admission`. When the bot meets Meet's "Gemini is taking notes" dialog the
join layer escalates `needs_help` with reason `consent_required`
(`modules/join/src/googlemeet/admission.ts`), but the machine allowed `needs_help` only from the
waiting room, so the escalation was refused and the user watched `joining` until the budget ran out.

This file pins the fix end to end on the receiver:

  * the machine accepts `joining -> needs_help`;
  * the escalation reason survives the terminal on the `needs_help` hop of `status_transition[]`;
  * a `failed` off a PRE-lobby `needs_help` is staged `joining` and evidenced `never_reached_lobby` /
    `system_fault`, never "the host did not admit" (`awaiting_admission_timeout` / `host_action`);
  * the same holds after a meeting-api restart (rehydrated record, persisted trail only);
  * downstream, `needs_help` is pre-active: a dead workload FAILS it (never `completed`), and a user
    stop keeps the status instead of writing `stopping` (which rehydrates as ACTIVE).

Negative control: the lobby path (`joining -> awaiting_admission -> needs_help -> failed`) still
stages `awaiting_admission`.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.machine import (
    BotStatus,
    FailureStage,
    LifecycleSink,
    MeetingStore,
    can_transition,
)

ENDPOINT = "/bots/internal/callback/lifecycle"
NO_INDICATORS = (
    "Bot failed to join the Google Meet meeting — no meeting indicators found within timeout"
)


def _seed(repo: InMemoryMeetingRepo, *, status: str, session_uid: str = "sess-uid", data=None) -> dict:
    m = asyncio.run(repo.create_meeting(
        user_id=1, platform="google_meet", native_meeting_id="abc-defg-hij", data=data or {},
    ))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=session_uid))
    repo.set_status(m["id"], status)
    return m


def _post(client: TestClient, **event):
    r = client.post(ENDPOINT, json={"connection_id": "sess-uid", **event})
    assert r.status_code == 200, r.text
    return r


def _failed_no_indicators(client: TestClient) -> None:
    _post(client, status="failed", exit_code=1, completion_reason="join_failure", reason=NO_INDICATORS)


# ── the machine ──────────────────────────────────────────────────────────────────────────────────


def test_machine_accepts_joining_to_needs_help():
    assert can_transition(BotStatus.JOINING, BotStatus.NEEDS_HELP)
    # Unchanged neighbours: the escalation still resolves only to active/failed.
    assert not can_transition(BotStatus.NEEDS_HELP, BotStatus.AWAITING_ADMISSION)
    assert not can_transition(BotStatus.NEEDS_HELP, BotStatus.COMPLETED)


# ── the seam: joining -> needs_help(consent_required) -> failed ──────────────────────────────────


def test_consent_gate_escalation_persists_reason_and_stays_ours():
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="requested")
    client = TestClient(create_app(open_callbacks=True, meeting_repo=repo))

    _post(client, status="joining")
    _post(client, status="needs_help", reason="consent_required")
    assert repo._meetings[m["id"]]["status"] == "needs_help"
    _failed_no_indicators(client)

    row = repo._meetings[m["id"]]
    data = row["data"]
    assert row["status"] == "failed"

    # The escalation's WHY is on its own hop, and survives the terminal's own reason.
    hops = [(e.get("from"), e.get("to")) for e in data["status_transition"]]
    assert hops == [(None, "joining"), ("joining", "needs_help"), ("needs_help", "failed")]
    help_hop = data["status_transition"][1]
    assert help_hop["reason"] == "consent_required"
    assert data["reason"] == NO_INDICATORS  # top-level reason is the terminal's, as before

    # Truthful attribution: never past `joining`, so not a lobby, not the host — ours.
    assert data["failure_stage"] == "joining"
    ev = data["join_evidence"]
    assert ev["stage"] == "joining"
    assert ev["reason"] == "never_reached_lobby"
    assert ev["attribution"] == "system_fault"


def test_lobby_escalation_still_stages_awaiting_admission():
    """Negative control: an escalation from the waiting room keeps its lobby."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="requested")
    client = TestClient(create_app(open_callbacks=True, meeting_repo=repo))

    _post(client, status="joining")
    _post(client, status="awaiting_admission")
    _post(client, status="needs_help", reason="waiting_room_timeout_approaching")
    _post(client, status="failed", exit_code=1, completion_reason="awaiting_admission_timeout",
          reason="Bot is still in the Google Meet waiting room after timeout")

    data = repo._meetings[m["id"]]["data"]
    assert data["failure_stage"] == "awaiting_admission"
    assert data["status_transition"][2]["reason"] == "waiting_room_timeout_approaching"
    assert data["join_evidence"]["reason"] == "awaiting_admission_timeout"


def test_needs_help_without_reason_records_no_reason_key():
    sink = LifecycleSink(store=MeetingStore())
    sink.apply({"connection_id": "c", "status": "joining"})
    rec = sink.apply({"connection_id": "c", "status": "needs_help"})
    assert "reason" not in rec.status_transition[-1]


# ── after a restart: the persisted trail, not in-process history, decides the stage ─────────────


def test_rehydrated_pre_lobby_needs_help_fails_at_joining():
    trail = [
        {"from": None, "to": "joining", "source": "bot_callback"},
        {"from": "joining", "to": "needs_help", "source": "bot_callback", "reason": "consent_required"},
    ]
    store = MeetingStore()
    store.rehydrate("c", "needs_help", {"status_transition": trail})
    rec = LifecycleSink(store=store).apply(
        {"connection_id": "c", "status": "failed", "exit_code": 1,
         "completion_reason": "join_failure", "reason": NO_INDICATORS}
    )
    assert rec.history == [BotStatus.FAILED]  # in-process history knows nothing of the lobby
    assert rec.failure_stage is FailureStage.JOINING
    assert rec.join_evidence["attribution"] == "system_fault"
    assert rec.status_transition[1]["reason"] == "consent_required"


def test_rehydrated_lobby_needs_help_fails_at_awaiting_admission():
    trail = [
        {"from": None, "to": "joining"},
        {"from": "joining", "to": "awaiting_admission"},
        {"from": "awaiting_admission", "to": "needs_help", "reason": "unknown_blocking_state"},
    ]
    store = MeetingStore()
    store.rehydrate("c", "needs_help", {"status_transition": trail})
    rec = LifecycleSink(store=store).apply(
        {"connection_id": "c", "status": "failed", "exit_code": 1,
         "completion_reason": "awaiting_admission_timeout"}
    )
    assert rec.failure_stage is FailureStage.AWAITING_ADMISSION


# ── downstream: needs_help is pre-active ─────────────────────────────────────────────────────────


def test_dead_workload_in_pre_lobby_needs_help_fails_not_completes():
    """A runtime-confirmed destroy of a bot waiting for help: it was never admitted, so `failed` —
    previously `needs_help` sat in the WAS-ACTIVE set and was `completed` (left_alone)."""
    from meeting_api.lifecycle.reconcile import synthesize_terminal_for_dead_workload

    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="requested")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-help"
    client = TestClient(create_app(open_callbacks=True, meeting_repo=repo))
    _post(client, status="joining")
    _post(client, status="needs_help", reason="consent_required")

    async def _drive(body: dict):
        assert "failure_stage" not in body  # not a lifecycle.v1 FailureStage — the receiver derives it
        assert "join_evidence" not in body  # the sweep cannot know how needs_help was entered
        return client.post(ENDPOINT, json=body).status_code

    assert asyncio.run(synthesize_terminal_for_dead_workload(
        repo, "wl-help", "destroyed", _drive, log=logging.getLogger("test.needs-help"),
    )) is True
    data = repo._meetings[m["id"]]["data"]
    assert repo._meetings[m["id"]]["status"] == "failed"
    assert data["failure_stage"] == "joining"
    assert data["join_evidence"]["attribution"] == "system_fault"


def test_user_stop_keeps_needs_help_instead_of_stopping():
    """`stopping` rehydrates as ACTIVE and is completed by the sweeps; writing it over a bot that
    never got in is the #807 laundering. A stop keeps `needs_help`."""
    from meeting_api.lifecycle.stop_router import _mark_stop_requested

    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="needs_help")
    asyncio.run(_mark_stop_requested(repo, dict(repo._meetings[m["id"]])))
    row = repo._meetings[m["id"]]
    assert row["status"] == "needs_help"
    assert row["data"].get("stop_requested") is True
