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
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# Cap forensics so a runaway ring-buffer can't bloat meeting.data (parent callbacks.py caps
# bot_logs at 50 KiB, trimming the OLDEST lines first). 50 * 1024 bytes.
_BOT_LOGS_BYTE_BUDGET = 50 * 1024


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


class TransitionSource(str, Enum):
    """What drove a status transition — rides the `meeting.status_change` webhook (webhook.v1).

    Mirrors the parent's `transition_source` argument to `schedule_status_webhook_task`
    (`bot_callback` / `user_stop`) plus the scheduler-timeout path the runtime scheduler drives
    (parent's max-bot-time leave command). `creation` stamps the very first transition.
    """

    CREATION = "creation"
    BOT_CALLBACK = "bot_callback"
    USER_STOP = "user_stop"
    SCHEDULER_TIMEOUT = "scheduler_timeout"


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


def _trim_bot_logs(lines: List[str]) -> tuple[List[str], bool]:
    """Cap the bot_logs ring-buffer at `_BOT_LOGS_BYTE_BUDGET`, trimming the OLDEST first.

    Faithful to the parent (`callbacks.py`): iterate from the newest line back, keep until the
    byte budget is spent, then restore chronological order. Returns (kept, truncated?).
    """
    kept: List[str] = []
    used = 0
    for line in reversed(lines):
        size = len(line.encode("utf-8")) + 1  # +1 for the implicit newline
        if used + size > _BOT_LOGS_BYTE_BUDGET and kept:
            return list(reversed(kept)), True
        kept.append(line)
        used += size
    return list(reversed(kept)), False


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

    P3a — the record carries the full lifecycle diagnostics: `error_details`, the
    `status_transition[]` trail (every hop with from/to/source/reason/timestamp — the
    parent's `update_meeting_status` entry), and the terminal forensics ring-buffer
    (`bot_logs` capped + `bot_resources` cgroup snapshot) landed into `data` exactly as
    the parent's `callbacks.py` writes them into `meeting.data`.
    """

    connection_id: str
    status: Optional[BotStatus] = None
    container_id: Optional[str] = None
    completion_reason: Optional[CompletionReason] = None
    failure_stage: Optional[FailureStage] = None
    reason: Optional[str] = None
    error_details: Optional[str] = None
    exit_code: Optional[int] = None
    history: list[BotStatus] = field(default_factory=list)
    # The forensics + transition trail the parent persists into `meeting.data` JSONB.
    status_transition: List[Dict[str, Any]] = field(default_factory=list)
    bot_logs: Optional[List[str]] = None
    bot_logs_truncated: bool = False
    bot_resources: Optional[Dict[str, Any]] = None
    # User intent (parent's `meeting.data.stop_requested`) — set by the DELETE/stop path, read
    # first by the exit classifier so a user stop is never mis-attributed as a failure.
    stop_requested: bool = False
    # Last transition's driver (rides the most recent status_change webhook).
    last_transition_source: Optional[TransitionSource] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def data(self) -> Dict[str, Any]:
        """The `meeting.data` JSONB projection — the forensics the parent persists there.

        Mirrors the parent's `meeting.data` keys (`status_transition`, `completion_reason`,
        `failure_stage`, `bot_logs`, `bot_resources`, `last_error`, `stop_requested`) so a
        recordings/transcript reader sees the same attribution shape it does in prod.
        """
        d: Dict[str, Any] = {"status_transition": list(self.status_transition)}
        if self.completion_reason is not None:
            d["completion_reason"] = self.completion_reason.value
        if self.failure_stage is not None:
            d["failure_stage"] = self.failure_stage.value
        if self.error_details is not None:
            d["last_error"] = {
                "exit_code": self.exit_code,
                "reason": self.reason,
                "error_details": self.error_details,
            }
        if self.bot_logs is not None:
            d["bot_logs"] = list(self.bot_logs)
            d["bot_logs_truncated"] = self.bot_logs_truncated
        if self.bot_resources is not None:
            d["bot_resources"] = dict(self.bot_resources)
        if self.stop_requested:
            d["stop_requested"] = True
        return d


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


@dataclass
class StatusChange:
    """The result of one FSM advance — carries the `meeting.status_change` webhook body (webhook.v1).

    `to_webhook_payload()` is the `data.status_change` block the parent's
    `schedule_status_webhook_task` emits: `{old_status, new_status, reason, transition_source}`.
    """

    record: MeetingRecord
    old_status: Optional[BotStatus]
    new_status: BotStatus
    reason: Optional[str]
    transition_source: TransitionSource

    def to_webhook_payload(self) -> Dict[str, Any]:
        return {
            "old_status": self.old_status.value if self.old_status is not None else None,
            "new_status": self.new_status.value,
            "reason": self.reason,
            "transition_source": self.transition_source.value,
        }


class LifecycleSink:
    """The port: ingest a (already seam-validated) lifecycle.v1 event, drive the FSM.

    `apply(event)` looks up / creates the record for `event["connection_id"]`, checks
    the transition against the machine, and either advances the record or raises
    `IllegalTransition`. On a terminal `failed`, `failure_stage` is derived from the
    record's CURRENT state (server-side, never the bot's payload value). On a terminal
    `completed`, the bot-reported `completion_reason` is recorded.

    P3a — every advance also: derives `error_details`, captures terminal forensics
    (`bot_logs` trimmed + `bot_resources`) into the record's `data`, appends an entry to
    the `status_transition[]` trail (with `transition_source`), and returns a `StatusChange`
    carrying the `meeting.status_change` webhook body. `apply_change(...)` returns the
    StatusChange; `apply(...)` returns the record (back-compat with the existing FSM eval).

    The event dict is the validated lifecycle.v1 `LifecycleEvent` (jsonschema-by-path
    happens at the HTTP seam / the machine eval, not here — this brick trusts the shape
    and owns only the transition logic).
    """

    def __init__(self, store: Optional[MeetingStore] = None):
        # `is None`, not `or` — an empty MeetingStore is falsy (len == 0).
        self.store = store if store is not None else MeetingStore()

    def apply(self, event: Dict[str, Any]) -> MeetingRecord:
        """Advance the FSM and return the record (back-compat). See `apply_change` for the
        StatusChange (webhook body)."""
        return self.apply_change(event).record

    def apply_change(
        self,
        event: Dict[str, Any],
        *,
        transition_source: TransitionSource = TransitionSource.BOT_CALLBACK,
    ) -> StatusChange:
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
        if event.get("error_details") is not None:
            rec.error_details = str(event["error_details"])

        if to is BotStatus.COMPLETED:
            raw = event.get("completion_reason")
            rec.completion_reason = CompletionReason(raw) if raw else None
        elif to is BotStatus.FAILED:
            # FM-003: derive failure_stage from the stage we were IN, not the payload.
            rec.failure_stage = _STATUS_TO_FAILURE_STAGE.get(frm, FailureStage.ACTIVE)
            raw = event.get("completion_reason")
            rec.completion_reason = CompletionReason(raw) if raw else None
            # The parent builds an `error_details` string on a failed exit when none supplied.
            if rec.error_details is None and (rec.exit_code is not None or rec.reason):
                rec.error_details = (
                    f"Bot exited with code {rec.exit_code}; reason: {rec.reason}"
                )

        # Terminal forensics → record.data (parent caps bot_logs, trims oldest-first).
        if to in _TERMINAL:
            if event.get("bot_logs"):
                rec.bot_logs, rec.bot_logs_truncated = _trim_bot_logs(list(event["bot_logs"]))
            if event.get("bot_resources"):
                rec.bot_resources = dict(event["bot_resources"])

        rec.status = to
        rec.history.append(to)
        rec.last_transition_source = transition_source

        # Append the status_transition[] trail entry (parent's update_meeting_status entry).
        entry: Dict[str, Any] = {
            "from": frm.value if frm is not None else None,
            "to": to.value,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": transition_source.value,
        }
        if rec.reason is not None and to in _TERMINAL:
            entry["reason"] = rec.reason
        if rec.completion_reason is not None:
            entry["completion_reason"] = rec.completion_reason.value
        if rec.failure_stage is not None:
            entry["failure_stage"] = rec.failure_stage.value
        if rec.error_details is not None:
            entry["error_details"] = rec.error_details
        rec.status_transition.append(entry)

        return StatusChange(
            record=rec,
            old_status=frm,
            new_status=to,
            reason=rec.reason,
            transition_source=transition_source,
        )
