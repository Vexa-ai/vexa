"""lifecycle — the meeting-state machine + the lifecycle.v1 receiver port.

Front door (P6): import from here, never a deep module path.

The bot emits `lifecycle.v1` LifecycleEvents to its control-plane callback (the
emitter side is `meetings/services/bot/src/orchestrator.ts`, L4-proven). This brick
is the RECEIVER: it ingests those events, drives each meeting record's FSM, and
rejects illegal transitions.

* ``BotStatus`` / ``CompletionReason`` / ``FailureStage`` — the sealed lifecycle.v1
  enums, re-expressed as Python enums.
* ``MeetingRecord`` — the in-memory record the FSM advances.
* ``MeetingStore`` — the record store: in-memory, keyed by ``connection_id``, holding no DB handle
  of its own. **That is true of the STORE and false of the BRICK.** This is the live bot-callback
  FSM in production, and its ``MeetingRecord.data`` projection is the SOLE writer of meeting
  attribution into Postgres: ``app.py`` posts the callback here, ``apply_change`` advances the
  record, and ``rec.data`` is handed to ``update_meeting_status(data=…)``, which shallow-merges
  every top-level key into the ``meetings.data`` JSONB column. Everything ``data`` projects — the
  reason, the completion reason, the failure stage, the join evidence — is what a customer's
  ``GET /meetings`` and every operator query read. Nothing else writes them.
* ``LifecycleSink`` — the port: ``apply(event)`` validates the seam + advances the FSM.
* ``IllegalTransition`` — raised (and surfaced as HTTP 409) on a forbidden transition.
* ``can_transition`` / ``LEGAL_TRANSITIONS`` — the machine, derived from the parent's
  ``schemas.get_valid_status_transitions`` reduced to the bot's domain lifecycle.
* ``StatusChange`` / ``TransitionSource`` (P3a) — one FSM advance's result + what drove it
  (``bot_callback`` / ``user_stop`` / ``scheduler_timeout``), carrying the
  ``meeting.status_change`` webhook body.
* ``build_status_change_envelope`` (P3a) — wrap a ``StatusChange`` as a sealed ``webhook.v1``
  ``Envelope`` (event_type ``meeting.status_change``).
* ``Disposition`` / ``disposition`` / ``may_dispatch_again`` — what a TERMINAL row leaves owed to
  its calendar occurrence (served · user-stopped · retry). The one table deciding whether a bot may
  go back into a meeting it has already been in; ``calendar_sync`` asks it before recreating a row.
* ``JoinFailureReason`` / ``JoinFailureAttribution`` (#1059/#1058) — the two evidence axes a
  ``failed`` pre-active meeting carries in ``data.join_evidence``: WHAT happened and WHO it belongs
  to. ``classify_join_failure`` / ``attribute_join_failure`` derive them; ``build_join_evidence``
  assembles the persisted block.
"""
from .join_evidence import (
    JoinFailureAttribution,
    JoinFailureReason,
    attribute_join_failure,
    build_join_evidence,
    classify_join_failure,
)
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
from .occurrence import (
    Disposition,
    disposition,
    may_dispatch_again,
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
    "JoinFailureAttribution",
    "JoinFailureReason",
    "attribute_join_failure",
    "build_join_evidence",
    "classify_join_failure",
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
    "Disposition",
    "disposition",
    "may_dispatch_again",
]
