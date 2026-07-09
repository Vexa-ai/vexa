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
* ``StatusChange`` / ``TransitionSource`` (P3a) — one FSM advance's result + what drove it
  (``bot_callback`` / ``user_stop`` / ``scheduler_timeout``), carrying the
  ``meeting.status_change`` webhook body.
* ``build_status_change_envelope`` (P3a) — wrap a ``StatusChange`` as a sealed ``webhook.v1``
  ``Envelope`` (event_type ``meeting.status_change``).
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
    StatusChange,
    TransitionSource,
    can_transition,
)
from .retry import (
    JoinRetryController,
    RetryClass,
    RetryOutcome,
    RetryPolicy,
    classify_retry,
    is_transient,
)
from .stop import (
    LeaveCommandPublisher,
    classify_user_stop,
    leave_command_channel,
    leave_command_payload,
    request_stop,
    stop_event_for,
)
from .webhook import build_status_change_envelope, build_typed_envelope, typed_event_type

__all__ = [
    "BotStatus",
    "CompletionReason",
    "FailureStage",
    "IllegalTransition",
    "LEGAL_TRANSITIONS",
    "LifecycleSink",
    "MeetingRecord",
    "MeetingStore",
    "StatusChange",
    "TransitionSource",
    "LeaveCommandPublisher",
    "JoinRetryController",
    "RetryClass",
    "RetryOutcome",
    "RetryPolicy",
    "build_status_change_envelope",
    "build_typed_envelope",
    "typed_event_type",
    "classify_retry",
    "classify_user_stop",
    "is_transient",
    "leave_command_channel",
    "leave_command_payload",
    "request_stop",
    "stop_event_for",
    "can_transition",
]
