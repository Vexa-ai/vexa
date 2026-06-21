"""lifecycle — the meeting-state machine + the lifecycle.v1 receiver port.

Front door (P6): import from here, never a deep module path.

The bot emits `lifecycle.v1` LifecycleEvents to its control-plane callback (the
emitter side is `meetings/services/bot/src/orchestrator.ts`, L4-proven). This brick
is the RECEIVER: it ingests those events, drives each meeting record's FSM, and
rejects illegal transitions.

* ``BotStatus`` / ``CompletionReason`` / ``FailureStage`` — the sealed lifecycle.v1
  enums, re-expressed as Python enums.
* ``MeetingRecord`` — the in-memory record the FSM advances.
* ``MeetingStore`` — an in-memory record store (no DB; the eval runs fully in-process).
* ``LifecycleSink`` — the port: ``apply(event)`` validates the seam + advances the FSM.
* ``IllegalTransition`` — raised (and surfaced as HTTP 409) on a forbidden transition.
* ``can_transition`` / ``LEGAL_TRANSITIONS`` — the machine, derived from the parent's
  ``schemas.get_valid_status_transitions`` reduced to the bot's domain lifecycle.
"""
from .machine import (
    BotStatus,
    CompletionReason,
    FailureStage,
    IllegalTransition,
    LEGAL_TRANSITIONS,
    LifecycleSink,
    MeetingRecord,
    MeetingStore,
    can_transition,
)

__all__ = [
    "BotStatus",
    "CompletionReason",
    "FailureStage",
    "IllegalTransition",
    "LEGAL_TRANSITIONS",
    "LifecycleSink",
    "MeetingRecord",
    "MeetingStore",
    "can_transition",
]
