"""O-MTG-1 eval (machine) — feed the lifecycle.v1 goldens through the FSM directly.

Asserts: goldens in legal order → correct transitions + terminal attribution; an illegal
transition (e.g. active→joining) is REJECTED; terminal records carry failure_stage +
completion_reason; a terminal record cannot be re-opened.
"""
from __future__ import annotations

import pytest

from meeting_api.lifecycle import (
    BotStatus,
    CompletionReason,
    FailureStage,
    IllegalTransition,
    LifecycleSink,
    MeetingStore,
    can_transition,
)


# --- the machine itself ----------------------------------------------------------------

def test_legal_edges():
    assert can_transition(None, BotStatus.JOINING)
    assert can_transition(BotStatus.JOINING, BotStatus.AWAITING_ADMISSION)
    assert can_transition(BotStatus.JOINING, BotStatus.ACTIVE)  # no waiting room
    assert can_transition(BotStatus.AWAITING_ADMISSION, BotStatus.ACTIVE)
    assert can_transition(BotStatus.AWAITING_ADMISSION, BotStatus.NEEDS_HELP)
    assert can_transition(BotStatus.NEEDS_HELP, BotStatus.ACTIVE)
    assert can_transition(BotStatus.ACTIVE, BotStatus.COMPLETED)
    assert can_transition(BotStatus.ACTIVE, BotStatus.FAILED)


def test_illegal_edges():
    assert not can_transition(BotStatus.ACTIVE, BotStatus.JOINING)  # the canonical illegal one
    assert not can_transition(BotStatus.COMPLETED, BotStatus.ACTIVE)  # terminal is terminal
    assert not can_transition(BotStatus.FAILED, BotStatus.ACTIVE)
    assert not can_transition(None, BotStatus.ACTIVE)  # first event must be `joining`
    assert not can_transition(BotStatus.JOINING, BotStatus.COMPLETED)  # must pass through active


# --- driving the goldens in legal order ------------------------------------------------

def test_goldens_legal_order(goldens):
    """joining → active → completed(stopped), feeding the actual sealed goldens."""
    sink = LifecycleSink(store=MeetingStore())

    rec = sink.apply(goldens["joining"])
    assert rec.status is BotStatus.JOINING
    assert rec.container_id == "mtg-abc123-bot"  # carried from the golden

    rec = sink.apply(goldens["active"])
    assert rec.status is BotStatus.ACTIVE

    rec = sink.apply(goldens["completed-stopped"])
    assert rec.status is BotStatus.COMPLETED
    assert rec.completion_reason is CompletionReason.STOPPED
    assert rec.is_terminal
    assert rec.history == [BotStatus.JOINING, BotStatus.ACTIVE, BotStatus.COMPLETED]


def test_failed_join_records_stage_and_reason(goldens):
    """joining → failed(awaiting_admission_rejected): the failed-join golden.

    The golden was reached from `joining`; failure_stage is derived SERVER-SIDE from the
    state we were in (JOINING) — NOT trusted from the payload's failure_stage field
    (which says 'awaiting_admission'). Mirrors the parent's FM-003 discipline.
    """
    sink = LifecycleSink(store=MeetingStore())
    sink.apply(goldens["joining"])
    rec = sink.apply(goldens["failed-join"])

    assert rec.status is BotStatus.FAILED
    assert rec.is_terminal
    # Derived from current state (was JOINING), not the payload's "awaiting_admission".
    assert rec.failure_stage is FailureStage.JOINING
    assert rec.completion_reason is CompletionReason.AWAITING_ADMISSION_REJECTED


def test_failed_from_awaiting_admission_stage(goldens):
    """joining → awaiting_admission → failed: failure_stage is AWAITING_ADMISSION."""
    sink = LifecycleSink(store=MeetingStore())
    sink.apply(goldens["joining"])
    sink.apply({"connection_id": "sess-uid", "status": "awaiting_admission"})
    rec = sink.apply(goldens["failed-join"])
    assert rec.failure_stage is FailureStage.AWAITING_ADMISSION


# --- rejecting illegal transitions -----------------------------------------------------

def test_illegal_transition_rejected(goldens):
    """active → joining is rejected (the canonical illegal transition)."""
    sink = LifecycleSink(store=MeetingStore())
    sink.apply(goldens["joining"])
    sink.apply(goldens["active"])

    with pytest.raises(IllegalTransition) as exc:
        sink.apply(goldens["joining"])  # active → joining
    assert exc.value.frm is BotStatus.ACTIVE
    assert exc.value.to is BotStatus.JOINING


def test_terminal_cannot_reopen(goldens):
    """A completed record rejects any further event."""
    sink = LifecycleSink(store=MeetingStore())
    sink.apply(goldens["joining"])
    sink.apply(goldens["active"])
    sink.apply(goldens["completed-stopped"])

    with pytest.raises(IllegalTransition):
        sink.apply(goldens["active"])  # completed → active


def test_first_event_must_be_joining(goldens):
    """A record's first event can only be `joining` — active-first is rejected."""
    sink = LifecycleSink(store=MeetingStore())
    with pytest.raises(IllegalTransition) as exc:
        sink.apply(goldens["active"])  # None → active
    assert exc.value.frm is None
    assert exc.value.to is BotStatus.ACTIVE
