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
    AUTH_SESSION_MISSING = "auth_session_missing"
    VALIDATION_ERROR = "validation_error"
    MAX_BOT_TIME_EXCEEDED = "max_bot_time_exceeded"


# The one terminal reason a user stop resolves to, at EVERY stage.
USER_STOP_REASON = CompletionReason.STOPPED.value


def dominant_completion_reason(reason: Any, *, stop_requested: bool) -> Any:
    """``stop_requested`` DOMINATES every completion-reason writer — one rule, one place.

    A stop that loses a race still ends the run, and the row must record WHY THE USER ENDED IT, not
    whichever fault the race happened to reach first. Witnessed on stage rev 193 (row 26313): a
    DELETE landed 0.4s after the POST, the bot was torn down before it could join, and the terminal
    was written ``join_failure`` — a reason ``retry.py`` classes TRANSIENT, so the occurrence stayed
    eligible for re-dispatch while ``data.stop_requested`` was true the whole time. The reason is the
    user's intent; the race is an accident of scheduling and has no business overwriting it.

    Founder ruling 2026-08-17: *"explicit stop must be stop, evict."*

    Applied at EVERY site that writes a terminal reason: the FSM advance
    (``LifecycleSink.apply_change`` below), the reconcile sweeps
    (``reconcile._pre_active_completion_reason`` + the was-active branch), and the by-id
    spawn-failure write (``MeetingRepo.fail_meeting``). ``lifecycle.occurrence`` then reads the
    result as ``USER_STOPPED`` — which is why the dominance has to hold at the WRITE, not only at
    the classification: a row whose reason says ``join_failure`` is re-dispatchable to every reader
    that never looks at the flag.
    """
    return USER_STOP_REASON if stop_requested else reason


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
    # A synthetic terminal driven by RUNTIME-CONFIRMED workload destruction (the runtime kernel
    # posted `state=destroyed`/`exited`/… for the bot's workload) rather than the bot's own callback.
    # This is real teardown evidence (#50's principle) that the run is over, and it is the ONLY source
    # permitted to force the terminal edge from a was-active/pre-active state whose in-process FSM
    # record is stale (e.g. the store still reads `joining` because the bot was stopped/SIGKILLed
    # before it could report `active`, while the DB user-stop moved the meeting to `stopping`). See
    # `LifecycleSink.apply_change(..., force_terminal_on_destroy=True)`.
    RUNTIME_DESTROY = "runtime_destroy"


# The machine. Reduced from the parent's `get_valid_status_transitions` to the bot's
# domain lifecycle: drop `requested`/`stopping` (server-side, not bot-emitted), keep the
# escalation path (`needs_help`, parent's `needs_human_help`). The bot's first emitted
# status is `joining`, so that is the machine's de-facto entry.
LEGAL_TRANSITIONS: Dict[Optional[BotStatus], frozenset[BotStatus]] = {
    None: frozenset({BotStatus.JOINING}),  # initial: a record's first event must be `joining`
    # needs_help straight from joining: a blocker (consent gate / captcha) can appear BEFORE the
    # lobby, so the bot must be able to escalate for a human without first reaching awaiting_admission.
    BotStatus.JOINING: frozenset(
        {BotStatus.AWAITING_ADMISSION, BotStatus.ACTIVE, BotStatus.NEEDS_HELP, BotStatus.FAILED}
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

# The persisted (DB) meeting status string → the in-memory FSM BotStatus, used to REHYDRATE a
# record after a meeting-api restart (LIFECYCLE-409 fix). The DB carries the SERVER-SIDE meeting
# status, a superset of the bot's domain `BotStatus`: `requested`/`stopping` are not bot states.
#   * `requested` → None (the FSM's pre-`joining` entry — the bot's first event must still be
#     `joining`, and None→JOINING is the only legal first edge).
#   * `stopping` → ACTIVE: `stopping` is the user-stop in-flight state (not a BotStatus); treating it
#     as ACTIVE keeps active/stopping → completed a LEGAL transition (the bot's terminal lands). This
#     is sound because the stop path writes `stopping` ONLY over a status in which the bot reached the
#     meeting — stopping a PRE-ACTIVE bot preserves its real stage, so this mapping can never launder
#     a never-admitted bot into ACTIVE (#807).
# Anything unrecognized maps to None (safe: forces the genuine-illegality check after reconciliation).
_PERSISTED_STATUS_TO_BOTSTATUS: Dict[str, Optional[BotStatus]] = {
    "requested": None,
    "joining": BotStatus.JOINING,
    "awaiting_admission": BotStatus.AWAITING_ADMISSION,
    "needs_help": BotStatus.NEEDS_HELP,
    "active": BotStatus.ACTIVE,
    "stopping": BotStatus.ACTIVE,  # server-side in-flight stop → treat as active so → completed stays legal
    "completed": BotStatus.COMPLETED,
    "failed": BotStatus.FAILED,
}


def bot_status_from_persisted(status: Optional[str]) -> Optional[BotStatus]:
    """Map a persisted DB meeting-status string → the FSM `BotStatus` for rehydration.

    Returns None for `requested`/unknown/None (the FSM's pre-`joining` entry). `stopping` (a
    server-side, non-bot state) maps to ACTIVE so a bot terminal off a user-stop stays legal.
    """
    if status is None:
        return None
    return _PERSISTED_STATUS_TO_BOTSTATUS.get(status)

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


#: Stages at which the bot had NOT yet reported reaching the waiting room. Used to answer
#: "did it ever reach the lobby?" from the FSM's own history — the single most load-bearing
#: discriminator in the taxonomy (#1058: lobby expiry vs never-got-there).
_PRE_LOBBY_STAGES = frozenset({FailureStage.REQUESTED, FailureStage.JOINING})


def _capture_join_evidence(
    rec: "MeetingRecord", event: Dict[str, Any], frm: Optional[BotStatus]
) -> Optional[Dict[str, Any]]:
    """Best-effort typed evidence for a `failed` terminal (#1059/#1058). NEVER raises.

    `reached_lobby` is answered from the record's OWN history, not the payload: a bot that reported
    `awaiting_admission` is a bot that stood in the waiting room, whatever its terminal event later
    claims about its stage (the orchestrator stamps a fixed `awaiting_admission` on every
    non-admitted verdict). Same discipline as `failure_stage`: derive server-side, never trust a
    stale payload field (FM-003).

    `needs_help` is deliberately NOT proof of a lobby on its own. It implies one only while
    `LEGAL_TRANSITIONS` permits reaching it from `awaiting_admission` alone — and #1251 adds
    `joining -> needs_help` precisely so a PRE-lobby blocker (a consent gate, a captcha) can
    escalate. The day that lands, a bot that never saw a waiting room would be stamped
    `reached_lobby` -> `awaiting_admission_timeout` -> `host_action`: filed as *the host did not let us in*,
    and excluded from `system_failure_rate` — the metric under-reporting our own defects in exactly
    the cohort #1251 exists to investigate. So the test is how `needs_help` was ENTERED, not that it
    occurred.
    """
    try:
        from .join_evidence import evidence_from_event

        reached_lobby = (
            BotStatus.AWAITING_ADMISSION in rec.history
            or frm is BotStatus.AWAITING_ADMISSION
            or (frm is BotStatus.NEEDS_HELP and BotStatus.AWAITING_ADMISSION in rec.history)
        )
        if rec.failure_stage is FailureStage.ACTIVE:
            # A bot that reached `active` was ADMITTED — whatever killed it afterwards (a pipeline
            # fault, an eviction) is not a join failure. The join taxonomy has nothing truthful to
            # say about it, so it says nothing rather than filing it under `unknown`.
            return None
        stage = rec.failure_stage.value if rec.failure_stage is not None else None
        return evidence_from_event(
            event, stage=stage, reached_lobby=reached_lobby, reason_text=rec.reason
        )
    except Exception:  # noqa: BLE001 — evidence is a report about a finished run; never fail on it
        return None


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
    #: What degraded the meeting without ending it — today the STT backend refusing chunks
    #: (kinds + counts + the backend's own detail), reported by the bot on the terminal event.
    stt_fault: Optional[Dict[str, Any]] = None
    #: WHY a pre-active run never reached the meeting (#1059/#1058) — the typed
    #: `join_evidence.JoinFailureReason` plus the stage, the stage timings, and the platform's own
    #: signal. Set on a `failed` terminal only; see `data` below for how it lands in the JSONB.
    join_evidence: Optional[Dict[str, Any]] = None
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

        THIS IS A WRITE PATH, not a view. The dict returned here is handed to
        `update_meeting_status(data=…)`, whose adapter shallow-merges every top-level key into the
        real `meetings.data` JSONB column — no allow-list, so a key added here reaches production and
        the public `GET /meetings` the moment it is projected. That is also why omission matters:
        a key this property does not emit is a fact no query can ever ask about, which is exactly
        how 83 `join_failure` rows came to carry `reason: None` (see the `reason` note below).
        """
        d: Dict[str, Any] = {"status_transition": list(self.status_transition)}
        if self.completion_reason is not None:
            d["completion_reason"] = self.completion_reason.value
        if self.failure_stage is not None:
            d["failure_stage"] = self.failure_stage.value
        # THE REASON, AT THE TOP LEVEL (#1059). Every producer already stamps one — the bot on each
        # terminal it emits, the reconcile sweep with the probe's own answer — and the record has
        # carried it on `self.reason` all along. It just never landed anywhere a reader looks: the
        # only projection was `last_error.reason`, a key the list view DROPS
        # (`projection.LIST_OMIT_KEYS`) and which `data->>'reason'` cannot see. That is the whole
        # mechanism behind "all 83 join_failure rows carry reason: None" — not a missing report, a
        # missing projection. Hoisting it costs nothing and makes the failure decomposable by query.
        if self.reason is not None:
            d["reason"] = self.reason
        if self.join_evidence is not None:
            d["join_evidence"] = dict(self.join_evidence)
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
        if self.stt_fault is not None:
            d["stt_fault"] = dict(self.stt_fault)
        return d


class MeetingStore:
    """In-memory record store, keyed by connection_id — it holds no DB handle.

    Read that narrowly: the STORE is in-process, the BRICK is not a simulation. Records advanced
    here are projected through `MeetingRecord.data` and merged into the `meetings.data` JSONB column
    by `update_meeting_status`, which is how production learns why a meeting ended. A record lost on
    restart is re-created from the DB status (`rehydrate`), not from here.
    """

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

    def rehydrate(
        self,
        connection_id: str,
        persisted_status: Optional[str],
        persisted_data: Optional[Dict[str, Any]] = None,
        *,
        replace_stale: bool = False,
    ) -> MeetingRecord:
        """Seed (or reconcile) the in-memory record from the DB's CURRENT meeting status.

        The store is in-memory, so a fresh process starts empty; seeding the record's status from the
        persisted DB status BEFORE applying an event lets the FSM reconcile against the real state
        (without it, a terminal event arriving at an empty store would start at status=None and be
        rejected as an illegal transition).

        Ordinarily this seeds only an empty record, so a lagging DB read cannot regress live local
        state. ``replace_stale`` is reserved for the HTTP adapter after a local transition was proven
        illegal; there the durable row may represent progress committed by another API replica.
        """
        rec = self.get_or_create(connection_id)
        # The user's stop intent is carried ALWAYS, outside the status guard, and only ever raised:
        # it is durable state the stop path wrote straight to the DB row (never through this FSM),
        # it is monotonic (nothing un-presses stop), and the terminal-reason rule below depends on
        # this record knowing it. Guarding it behind `rec.status is None` was the F3 hole — a record
        # already advanced to `joining` in this process never learned the DELETE had landed, so its
        # terminal was written with the bot's own `join_failure`.
        if isinstance(persisted_data, dict) and persisted_data.get("stop_requested"):
            rec.stop_requested = True
        if rec.status is None or replace_stale:
            seeded = bot_status_from_persisted(persisted_status)
            if seeded is not None:
                rec.status = seeded
            if isinstance(persisted_data, dict):
                trail = persisted_data.get("status_transition")
                if isinstance(trail, list):
                    rec.status_transition = [
                        dict(entry) for entry in trail if isinstance(entry, dict)
                    ]
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
    # True when the event was a redelivery of the record's CURRENT status (idempotent no-op) — the
    # FSM did not actually advance. The HTTP seam returns 200 (not 409) but skips the side effects
    # that should fire ONLY on a genuine advance (re-persist / re-deliver are harmless but pointless).
    no_op: bool = False

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

    @staticmethod
    def _terminal_reason(
        rec: MeetingRecord, event: Dict[str, Any]
    ) -> Optional[CompletionReason]:
        """The reason to record on a terminal advance — the bot's, unless the USER stopped it.

        The bot reports what IT saw (it was killed mid-join → ``join_failure``). When the record
        carries the user's stop intent, that intent is the truth about why the run ended, and it
        wins here at the WRITE — see ``dominant_completion_reason``."""
        raw = event.get("completion_reason")
        raw = dominant_completion_reason(raw, stop_requested=rec.stop_requested)
        return CompletionReason(raw) if raw else None

    def apply_change(
        self,
        event: Dict[str, Any],
        *,
        transition_source: TransitionSource = TransitionSource.BOT_CALLBACK,
        force_terminal_on_destroy: bool = False,
    ) -> StatusChange:
        """Advance the FSM for `event`, returning the resulting `StatusChange`.

        `force_terminal_on_destroy` (only ever set by the runtime-destroy synthetic-terminal path,
        `TransitionSource.RUNTIME_DESTROY`) permits the terminal edge (`completed`/`failed`) from ANY
        non-terminal FSM state — including one from which a bot-driven terminal would be illegal (e.g.
        `joining → completed`, when the store still reads `joining` because the bot was stopped/killed
        in the waiting room before it could report `active`, while the DB user-stop already moved the
        meeting to `stopping`). This is safe and narrow: it fires ONLY on runtime-confirmed workload
        destruction (real teardown evidence), ONLY targets a terminal state, and STILL respects
        idempotency + terminal-is-terminal below (a completed/failed record is never re-opened, and a
        DIFFERENT terminal on an already-terminal record is still rejected). Bot-driven transitions
        (the default) are unaffected — the machine is not loosened for real lifecycle events."""
        connection_id = event["connection_id"]
        to = BotStatus(event["status"])
        rec = self.store.get_or_create(connection_id)

        # IDEMPOTENCY (LIFECYCLE-409 fix): a redelivery of the record's CURRENT status is a no-op
        # 200, not a 409. The bot retries its terminal callback up to 3x; a second `completed` after
        # the first one landed (or after rehydration seeded `completed`) must succeed, not error.
        # This covers BOTH a non-terminal same-status replay AND a terminal-at-the-requested-terminal
        # replay (rec.is_terminal and rec.status == to). A DIFFERENT terminal on an already-terminal
        # record is still genuinely illegal (handled below).
        if rec.status == to:
            return StatusChange(
                record=rec,
                old_status=rec.status,
                new_status=to,
                reason=rec.reason,
                transition_source=transition_source,
                no_op=True,
            )

        if rec.is_terminal:
            # Terminal is terminal — no event re-opens a completed/failed record (and a transition to
            # a DIFFERENT terminal is rejected). Same-terminal redelivery handled by the no-op above.
            # This holds even for a runtime-destroy synthetic terminal: the bot's own terminal callback
            # (or a prior reap) is authoritative once it has landed.
            raise IllegalTransition(connection_id, rec.status, to)

        # RUNTIME-DESTROY synthetic terminal: real teardown evidence forces the terminal edge from any
        # NON-terminal state, even one from which a bot-driven terminal would be illegal (e.g.
        # `joining → completed` on a bot stopped/killed before it reported `active`). Without this the
        # synthetic `completed`/`failed` the runtime-callback drives is rejected 409, the meeting stays
        # `stopping`, and the stop-reconcile sweep re-DELETEs (now 404) every ~15s FOREVER (the reaper
        # loop). Guarded: ONLY a terminal target, ONLY this source — never loosens bot-driven edges.
        if force_terminal_on_destroy and to in _TERMINAL:
            pass  # legal by teardown evidence; fall through to the advance
        elif not can_transition(rec.status, to):
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
            rec.completion_reason = self._terminal_reason(rec, event)
        elif to is BotStatus.FAILED:
            # FM-003: derive failure_stage from the stage we were IN, not the payload.
            rec.failure_stage = _STATUS_TO_FAILURE_STAGE.get(frm, FailureStage.ACTIVE)
            rec.completion_reason = self._terminal_reason(rec, event)
            # The parent builds an `error_details` string on a failed exit when none supplied.
            if rec.error_details is None and (rec.exit_code is not None or rec.reason):
                rec.error_details = (
                    f"Bot exited with code {rec.exit_code}; reason: {rec.reason}"
                )
            # TYPED join evidence (#1058) — the diagnostic axis alongside the sealed
            # `completion_reason`, so the ~20s platform refusal and the ~13min lobby expiry that
            # both read `join_failure` become two countable populations. Prefer the producer's own
            # verdict (the bot holds the platform signal and the stage timings); derive one from the
            # record when it sent none, so a reconcile-driven or runtime-destroy terminal is
            # evidenced too. FAIL-OPEN by contract: this is a REPORT about a run that has already
            # ended, and no fault in it may alter the terminal the FSM is recording.
            rec.join_evidence = _capture_join_evidence(rec, event, frm)

        # Terminal forensics → record.data (parent caps bot_logs, trims oldest-first).
        if to in _TERMINAL:
            if event.get("bot_logs"):
                rec.bot_logs, rec.bot_logs_truncated = _trim_bot_logs(list(event["bot_logs"]))
            if event.get("bot_resources"):
                rec.bot_resources = dict(event["bot_resources"])
            # WHY a transcript is short or empty. The bot counts STT failures across the meeting
            # and reports them once, here — without it a meeting whose backend refused every chunk
            # completes indistinguishable from a silent room (the zero-segment shape). Additive on
            # lifecycle.v1 (additionalProperties: true), same as infra_fault.
            if event.get("stt_fault"):
                rec.stt_fault = dict(event["stt_fault"])

        rec.status = to
        rec.history.append(to)
        rec.last_transition_source = transition_source

        # Append the status_transition[] trail entry (parent's update_meeting_status entry).
        producer_timestamp = event.get("timestamp")
        entry: Dict[str, Any] = {
            "from": frm.value if frm is not None else None,
            "to": to.value,
            # The producer observed admission/departure. Callback receipt time may be delayed by
            # retries, so it cannot be the billing clock. Legacy events without event time retain
            # the server fallback but will not satisfy the provenance contract used for billing.
            "timestamp": producer_timestamp
            or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "timestamp_source": "producer" if producer_timestamp else "receiver",
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
