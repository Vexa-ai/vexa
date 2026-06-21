"""The meeting-state machine + the LifecycleSink port.

Derived from real parent behavior (`services/meeting-api/meeting_api/schemas.py`
`get_valid_status_transitions` + `callbacks.py`), reimplemented clean for the bot's
DOMAIN lifecycle (lifecycle.v1's `BotStatus`), not the server-side meeting status
(which also has `requested`/`stopping`).

The lifecycle.v1 README documents the machine; this is the machine-checked
`can_transition`. The sink is the receiver: it validates each event at the seam
(the caller does jsonschema-by-path against the sealed schema) and advances the FSM,
rejecting illegal transitions and recording terminal attribution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class BotStatus(str, Enum):
    """lifecycle.v1 `BotStatus` — the bot's DOMAIN status (not the container's)."""

    JOINING = "joining"
    AWAITING_ADMISSION = "awaiting_admission"
    ACTIVE = "active"
    NEEDS_HELP = "needs_help"
    COMPLETED = "completed"
    FAILED = "failed"


class CompletionReason(str, Enum):
    """lifecycle.v1 `CompletionReason` — why a `completed` run ended."""

    STOPPED = "stopped"
    LEFT_ALONE = "left_alone"
    STARTUP_ALONE = "startup_alone"
    EVICTED = "evicted"
    AWAITING_ADMISSION_TIMEOUT = "awaiting_admission_timeout"
    AWAITING_ADMISSION_REJECTED = "awaiting_admission_rejected"
    JOIN_FAILURE = "join_failure"
    VALIDATION_ERROR = "validation_error"
    MAX_BOT_TIME_EXCEEDED = "max_bot_time_exceeded"


class FailureStage(str, Enum):
    """lifecycle.v1 `FailureStage` — furthest stage reached, for `failed` attribution."""

    REQUESTED = "requested"
    JOINING = "joining"
    AWAITING_ADMISSION = "awaiting_admission"
    ACTIVE = "active"


# The machine. Reduced from the parent's `get_valid_status_transitions` to the bot's
# domain lifecycle: drop `requested`/`stopping` (server-side, not bot-emitted), keep the
# escalation path (`needs_help`, parent's `needs_human_help`). The bot's first emitted
# status is `joining`, so that is the machine's de-facto entry.
LEGAL_TRANSITIONS: Dict[Optional[BotStatus], frozenset[BotStatus]] = {
    None: frozenset({BotStatus.JOINING}),  # initial: a record's first event must be `joining`
    BotStatus.JOINING: frozenset(
        {BotStatus.AWAITING_ADMISSION, BotStatus.ACTIVE, BotStatus.FAILED}
    ),
    BotStatus.AWAITING_ADMISSION: frozenset(
        {BotStatus.ACTIVE, BotStatus.NEEDS_HELP, BotStatus.FAILED}
    ),
    BotStatus.NEEDS_HELP: frozenset({BotStatus.ACTIVE, BotStatus.FAILED}),
    BotStatus.ACTIVE: frozenset({BotStatus.COMPLETED, BotStatus.FAILED}),
    BotStatus.COMPLETED: frozenset(),  # terminal
    BotStatus.FAILED: frozenset(),  # terminal
}

_TERMINAL = frozenset({BotStatus.COMPLETED, BotStatus.FAILED})

# Stage a record was in maps to the FailureStage to record if it terminates `failed`.
# (Mirrors the parent's `_failure_stage_from_status`: derive server-side from current
# state, never trust the bot's stale payload value.)
_STATUS_TO_FAILURE_STAGE: Dict[Optional[BotStatus], FailureStage] = {
    None: FailureStage.REQUESTED,
    BotStatus.JOINING: FailureStage.JOINING,
    BotStatus.AWAITING_ADMISSION: FailureStage.AWAITING_ADMISSION,
    BotStatus.NEEDS_HELP: FailureStage.AWAITING_ADMISSION,
    BotStatus.ACTIVE: FailureStage.ACTIVE,
}


def can_transition(frm: Optional[BotStatus], to: BotStatus) -> bool:
    """Is `frm → to` a legal transition of the meeting FSM?"""
    return to in LEGAL_TRANSITIONS.get(frm, frozenset())


class IllegalTransition(Exception):
    """Raised when a lifecycle event would drive an illegal FSM transition.

    Carries the offending edge so the HTTP seam can surface it (parent returns a
    `{"status": "error", "detail": "Invalid transition: ..."}` body; the receiver
    endpoint maps this to 409).
    """

    def __init__(self, connection_id: str, frm: Optional[BotStatus], to: BotStatus):
        self.connection_id = connection_id
        self.frm = frm
        self.to = to
        frm_v = frm.value if frm is not None else "<new>"
        super().__init__(f"Invalid transition: {frm_v} → {to.value} (connection_id={connection_id})")


@dataclass
class MeetingRecord:
    """The in-memory meeting record the FSM advances.

    One per `connection_id` (the session uid). `status` is None until the first
    `joining` event lands. Terminal attribution (`completion_reason`, `failure_stage`)
    is recorded server-side, not trusted from the bot payload — same discipline as the
    parent's `_failure_stage_from_status` (FM-003).
    """

    connection_id: str
    status: Optional[BotStatus] = None
    container_id: Optional[str] = None
    completion_reason: Optional[CompletionReason] = None
    failure_stage: Optional[FailureStage] = None
    reason: Optional[str] = None
    exit_code: Optional[int] = None
    history: list[BotStatus] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL


class MeetingStore:
    """In-memory record store, keyed by connection_id. No DB — the eval is in-process."""

    def __init__(self) -> None:
        self._records: Dict[str, MeetingRecord] = {}

    def get(self, connection_id: str) -> Optional[MeetingRecord]:
        return self._records.get(connection_id)

    def get_or_create(self, connection_id: str) -> MeetingRecord:
        rec = self._records.get(connection_id)
        if rec is None:
            rec = MeetingRecord(connection_id=connection_id)
            self._records[connection_id] = rec
        return rec

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._records)


class LifecycleSink:
    """The port: ingest a (already seam-validated) lifecycle.v1 event, drive the FSM.

    `apply(event)` looks up / creates the record for `event["connection_id"]`, checks
    the transition against the machine, and either advances the record or raises
    `IllegalTransition`. On a terminal `failed`, `failure_stage` is derived from the
    record's CURRENT state (server-side, never the bot's payload value). On a terminal
    `completed`, the bot-reported `completion_reason` is recorded.

    The event dict is the validated lifecycle.v1 `LifecycleEvent` (jsonschema-by-path
    happens at the HTTP seam / the machine eval, not here — this brick trusts the shape
    and owns only the transition logic).
    """

    def __init__(self, store: Optional[MeetingStore] = None):
        # `is None`, not `or` — an empty MeetingStore is falsy (len == 0).
        self.store = store if store is not None else MeetingStore()

    def apply(self, event: Dict[str, Any]) -> MeetingRecord:
        connection_id = event["connection_id"]
        to = BotStatus(event["status"])
        rec = self.store.get_or_create(connection_id)

        if rec.is_terminal:
            # Terminal is terminal — no event re-opens a completed/failed record.
            raise IllegalTransition(connection_id, rec.status, to)

        if not can_transition(rec.status, to):
            raise IllegalTransition(connection_id, rec.status, to)

        frm = rec.status

        if event.get("container_id"):
            rec.container_id = event["container_id"]
        if event.get("reason") is not None:
            rec.reason = event["reason"]
        if event.get("exit_code") is not None:
            rec.exit_code = event["exit_code"]

        if to is BotStatus.COMPLETED:
            raw = event.get("completion_reason")
            rec.completion_reason = CompletionReason(raw) if raw else None
        elif to is BotStatus.FAILED:
            # FM-003: derive failure_stage from the stage we were IN, not the payload.
            rec.failure_stage = _STATUS_TO_FAILURE_STAGE.get(frm, FailureStage.ACTIVE)
            raw = event.get("completion_reason")
            rec.completion_reason = CompletionReason(raw) if raw else None

        rec.status = to
        rec.history.append(to)
        return rec
