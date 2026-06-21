"""P3b eval — three lifecycle fixtures (prove three scenarios).

A fixture per scenario, asserting the resulting status + reason + (for the stop) the leave-command
publish. OFFLINE — in-memory FSM + fakeredis (no docker, no meeting, no network).

  1. **normal**: joining → awaiting_admission → active → completed(stopped).
  2. **start-then-immediate-user-stop**: a DELETE/stop while the meeting is still joining /
     awaiting_admission → set `stop_requested` + publish a `bot_commands:meeting:{id}` leave command
     → the bot's exit is classified to a TERMINAL state WITH a reason (completed/stopped), and the
     FSM transition is stamped `transition_source=user_stop` — not a silent jump.
  3. **join-failure**: awaiting_admission → failed with `failure_stage=awaiting_admission` +
     `completion_reason=awaiting_admission_rejected`.
"""
from __future__ import annotations

import json

import pytest

from meeting_api.lifecycle import (
    BotStatus,
    CompletionReason,
    FailureStage,
    LifecycleSink,
    MeetingStore,
    TransitionSource,
    leave_command_channel,
    request_stop,
    stop_event_for,
)


# ── 1. normal ──────────────────────────────────────────────────────────────────────────────────

def test_fixture_normal():
    """joining → awaiting_admission → active → completed(stopped)."""
    sink = LifecycleSink(store=MeetingStore())
    cid = "sess-normal"
    sink.apply({"connection_id": cid, "container_id": "c1", "status": "joining"})
    sink.apply({"connection_id": cid, "status": "awaiting_admission"})
    sink.apply({"connection_id": cid, "status": "active"})
    change = sink.apply_change(
        {"connection_id": cid, "status": "completed", "exit_code": 0,
         "completion_reason": "stopped"},
    )
    rec = change.record
    assert rec.status is BotStatus.COMPLETED
    assert rec.completion_reason is CompletionReason.STOPPED
    assert rec.failure_stage is None
    assert rec.is_terminal
    assert rec.history == [
        BotStatus.JOINING, BotStatus.AWAITING_ADMISSION, BotStatus.ACTIVE, BotStatus.COMPLETED,
    ]
    # the trail records every hop, all bot_callback-sourced
    assert [e["to"] for e in rec.status_transition] == [
        "joining", "awaiting_admission", "active", "completed",
    ]
    assert {e["source"] for e in rec.status_transition} == {"bot_callback"}


# ── 2. start-then-immediate-user-stop ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stop_at", ["joining", "awaiting_admission"])
async def test_fixture_user_stop_publishes_leave_and_terminates_with_reason(fake_redis, stop_at):
    """A DELETE while the meeting is still joining/awaiting_admission.

    Asserts: (a) `stop_requested` is set; (b) a `bot_commands:meeting:{id}` `{action:leave}` command
    is PUBLISHED; (c) the bot's subsequent exit is classified to a TERMINAL state WITH a reason
    (completed/stopped); (d) that transition is stamped `transition_source=user_stop`.
    """
    sink = LifecycleSink(store=MeetingStore())
    cid, meeting_id = "sess-stop", 4242

    # Bring the bot up to the pre-active stage.
    sink.apply({"connection_id": cid, "container_id": "c1", "status": "joining"})
    if stop_at == "awaiting_admission":
        sink.apply({"connection_id": cid, "status": "awaiting_admission"})
    rec = sink.store.get(cid)
    assert not rec.is_terminal  # still in-flight when the user stops

    # Subscribe so we can capture the published leave command off fakeredis.
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(leave_command_channel(meeting_id))

    # (a)+(b): request the stop — sets stop_requested, publishes the leave command.
    cmd = await request_stop(rec, fake_redis, meeting_id=meeting_id)
    assert rec.stop_requested is True
    assert cmd["channel"] == f"bot_commands:meeting:{meeting_id}"
    assert cmd["payload"] == {"action": "leave", "meeting_id": meeting_id}

    # the message actually went onto the channel (fakeredis pub/sub)
    await pubsub.get_message(timeout=0.1)  # drop the subscribe ack
    msg = await pubsub.get_message(timeout=0.5)
    assert msg is not None and msg["type"] == "message"
    assert json.loads(msg["data"]) == {"action": "leave", "meeting_id": meeting_id}

    # (c)+(d): the bot honours the leave and emits its terminal event → classified WITH a reason,
    # the transition stamped user_stop (not a silent jump to terminal). A pre-active stop lands on
    # the FSM-legal terminal `failed` (the only terminal reachable from joining/awaiting_admission)
    # but ALWAYS carries completion_reason=stopped — an ATTRIBUTED terminal, never silent.
    change = sink.apply_change(stop_event_for(rec), transition_source=TransitionSource.USER_STOP)
    assert change.record.is_terminal                              # TERMINAL
    assert change.record.status is BotStatus.FAILED              # FSM-legal pre-active terminal
    assert change.record.completion_reason is CompletionReason.STOPPED  # WITH a reason (attributed)
    assert change.transition_source is TransitionSource.USER_STOP
    assert change.to_webhook_payload()["transition_source"] == "user_stop"
    assert change.record.status_transition[-1]["source"] == "user_stop"
    assert change.record.status_transition[-1]["completion_reason"] == "stopped"
    assert change.record.data["stop_requested"] is True


# ── 3. join-failure ──────────────────────────────────────────────────────────────────────────────

def test_fixture_join_failure_records_stage_and_reason():
    """awaiting_admission → failed with the right failure_stage + reason."""
    sink = LifecycleSink(store=MeetingStore())
    cid = "sess-fail"
    sink.apply({"connection_id": cid, "container_id": "c1", "status": "joining"})
    sink.apply({"connection_id": cid, "status": "awaiting_admission"})
    change = sink.apply_change(
        {"connection_id": cid, "status": "failed", "exit_code": 1,
         "failure_stage": "active",  # stale payload — must be ignored
         "completion_reason": "awaiting_admission_rejected", "reason": "host denied admission"},
    )
    rec = change.record
    assert rec.status is BotStatus.FAILED
    assert rec.is_terminal
    assert rec.failure_stage is FailureStage.AWAITING_ADMISSION  # server-derived, not "active"
    assert rec.completion_reason is CompletionReason.AWAITING_ADMISSION_REJECTED
    # error_details is synthesized server-side on a failed exit (lifecycle.v1 carries none).
    assert rec.error_details
    assert rec.data["last_error"]["error_details"]


def test_fixture_join_failure_timeout_stage_joining():
    """A timeout while still in `joining` records failure_stage=joining."""
    sink = LifecycleSink(store=MeetingStore())
    cid = "sess-fail2"
    sink.apply({"connection_id": cid, "container_id": "c1", "status": "joining"})
    rec = sink.apply(
        {"connection_id": cid, "status": "failed", "exit_code": 1,
         "completion_reason": "awaiting_admission_timeout", "reason": "no host joined"},
    )
    assert rec.failure_stage is FailureStage.JOINING
    assert rec.completion_reason is CompletionReason.AWAITING_ADMISSION_TIMEOUT
