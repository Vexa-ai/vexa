"""Ports (Protocols) for the bot-spawn flow — the seams that let the SAME ``build_router`` /
``request_bot`` run with real adapters (SQLAlchemy + the runtime.v1 HTTP kernel) in production
and in-process fakes in tests.

``POST /bots`` talks to two collaborators (the parent ``meetings.request_bot``):

  * **the meeting store** — insert the ``Meeting`` row (status ``requested``), eager-create the
    ``MeetingSession`` keyed by the bot's ``connectionId``, and write the resolved ``bot_container_id``
    back once the kernel reports the workload name.
  * **the runtime kernel** — spawn the meeting-bot workload over ``runtime.v1`` (``POST /workloads``
    with a ``WorkloadSpec``), returning the workload id / name. Quota-exceeded surfaces 429.

Each is a ``typing.Protocol`` so the app depends on BEHAVIOR, not a concrete client. ``adapters.py``
supplies the production implementations; the module's tests supply in-process fakes.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class MeetingRepo(Protocol):
    """The DB side of ``POST /bots``: dedup, insert, eager session, container write-back.

    Mirrors the SQL the parent ``meetings.request_bot`` runs against ``meetings`` /
    ``meeting_sessions`` (recordings/notes live in ``meetings.data`` JSONB — no separate table).
    """

    async def find_active(self, user_id: int, platform: str, native_meeting_id: str) -> Optional[dict]:
        """The user's already-active/-requested meeting for ``(platform, native_id)`` (dedup
        boundary — a non-None result means ``POST /bots`` returns 409), or ``None``.

        ACTIVE = status in ``{requested, joining, awaiting_admission, active}`` (parent's non-terminal
        set; ``stopping`` is in-flight too — see ``_ACTIVE_STATUSES``)."""
        ...

    async def find_latest(self, user_id: int, platform: str, native_meeting_id: str) -> Optional[dict]:
        """The user's MOST-RECENT meeting for ``(platform, native_id)`` regardless of status, or
        ``None``. ``continue_meeting`` reuses this row when it is TERMINAL (completed/failed)."""
        ...

    async def create_meeting(
        self, *, user_id: int, platform: str, native_meeting_id: str, data: dict
    ) -> dict:
        """Insert a ``Meeting`` row (status ``requested``) and return it as a dict (``id``,
        ``status``, ``created_at`` …) — the row the response is built from."""
        ...

    async def create_meeting_guarded(
        self,
        *,
        user_id: int,
        platform: str,
        native_meeting_id: str,
        data: dict,
        max_concurrent: Optional[int] = None,
        exclude_meeting_id: Optional[int] = None,
    ) -> dict:
        """ATOMIC dedup + cap-check + insert — the TOCTOU-safe spawn primitive (ROB1/ROB2).

        A ``max_concurrent <= 0`` means the user's quota is DEPLETED (0 = no bots, never
        "unlimited"): raise ``MaxBotsExceeded`` immediately, before any of the steps below.
        Only ``max_concurrent=None`` (no cap provided) skips the cap gate.

        Performs, in a SINGLE transaction with NO yield point between the checks and the insert:
          1. dedup — if the user already has an ACTIVE row for ``(platform, native_meeting_id)``,
             raise ``DuplicateMeeting`` (→ HTTP 409);
          2. cap — if ``max_concurrent`` is set and the user already has ``>= max_concurrent`` ACTIVE
             bots (``browser_session`` excluded; ``exclude_meeting_id`` not counted), raise
             ``MaxBotsExceeded`` (→ HTTP 429);
          3. insert the ``Meeting`` row (status ``requested``) and return it as a dict.

        The real (SQLAlchemy) adapter serializes concurrent spawns for the SAME user with a per-user
        ``pg_advisory_xact_lock`` and backstops dedup with a unique partial index on active rows; the
        in-memory fake performs the check+insert with no ``await`` between them so the race closes
        offline too. Replaces the old separate ``find_active`` + ``count_active_bots`` +
        ``create_meeting`` pre-check sequence on the fresh-insert path."""
        ...

    async def reopen_meeting(self, *, meeting_id: int) -> dict:
        """Reset a TERMINAL meeting row back to ``requested`` for a continued run (``continue_meeting``):
        clear the prior terminal attribution, keep the row id (so transcripts/recordings keyed by it
        survive). Returns the updated row."""
        ...

    async def create_session(self, *, meeting_id: int, session_uid: str) -> None:
        """Eager-create the ``MeetingSession`` keyed by ``session_uid`` (== the bot's
        ``connectionId``), so a recording upload resolves its meeting before the bot reports
        ``active`` (parent ``meetings.py`` MeetingSession insert). N sessions accumulate per
        meeting (one per bot connection / continued run)."""
        ...

    async def list_sessions(self, *, meeting_id: int) -> list:
        """All ``session_uid``s for a meeting, oldest-first — the sessions the response lists."""
        ...

    async def set_bot_container(self, *, meeting_id: int, bot_container_id: str) -> dict:
        """Record the kernel-assigned workload id/name on the meeting and return the updated row."""
        ...

    async def mark_spawn_rejected(
        self, *, meeting_id: int, reason: str, data: Optional[dict] = None
    ) -> Optional[dict]:
        """Make a pre-workload runtime rejection terminal and merge content-free attribution."""
        ...

    async def withdraw_capture(
        self,
        *,
        tenant_id: str,
        user_id: int,
        platform: str,
        native_meeting_id: str,
        withdrawn_at: str,
    ) -> Optional[dict]:
        """Durably withdraw one owner/tenant-bound ZAKI capture under the meeting write barrier.

        Returns ``{meeting, changed, should_stop, prior_status}``, or ``None`` without revealing
        whether a differently scoped meeting exists.
        """
        ...

    async def count_active_bots(self, *, user_id: int, exclude_meeting_id: Optional[int] = None) -> int:
        """Count the user's ACTIVE (non-terminal) bots for the max-bots quota (P3e).

        EXCLUDES infra ``browser_session`` workloads (parent ``meetings.py:1091``
        ``Meeting.platform != "browser_session"``). The active set is
        ``{requested, joining, awaiting_admission, active}``. ``exclude_meeting_id`` lets a
        ``continue_meeting`` reopen not double-count the row it is about to reuse."""
        ...

    async def get_status_by_session(self, *, session_uid: str) -> Optional[str]:
        """Resolve ``session_uid`` (== the bot's ``connectionId``) → the meeting's CURRENT persisted
        status string, or ``None`` for an unknown session. Used to REHYDRATE the in-memory lifecycle
        FSM after a meeting-api restart (LIFECYCLE-409): the in-memory store is non-durable, so the
        callback reconciles a fresh/empty record against the DB's real status BEFORE applying the
        bot's event (else a terminal event on an empty store creates a status=None record and 409s)."""
        ...

    async def update_meeting_status(
        self,
        *,
        session_uid: str,
        status: str,
        completion_reason: Optional[str] = None,
        failure_stage: Optional[str] = None,
        data: Optional[dict] = None,
    ) -> Optional[dict]:
        """Persist a bot ``lifecycle.v1`` advance to the DB meeting row + RETURN the updated row dict
        (incl. ``data`` — so the lifecycle callback can deliver the per-user webhook from
        ``meeting.data`` without a second read), or ``None`` for an unknown session. Set
        ``status`` and merge ``completion_reason`` / ``failure_stage`` + the receiver's forensics into
        ``meeting.data`` JSONB. Maps ``session_uid`` (== the bot's ``connectionId``) → meeting via
        ``meeting_sessions``; a no-op for an unknown session (e.g. a self-host bot). So the live FSM is
        DURABLE + QUERYABLE (``GET /meetings`` reflects it, survives a restart) — not only the
        in-process ``MeetingStore``."""
        ...


@runtime_checkable
class RuntimeClient(Protocol):
    """The runtime.v1 spawn hop. ``create_workload`` POSTs a ``WorkloadSpec`` to the kernel's
    ``POST /workloads`` and returns ``{"workloadId": ..., "state": ...}`` (the parent's
    ``_spawn_via_runtime_api`` over ``POST /containers``). Raises ``QuotaExceeded`` on 429."""

    async def create_workload(self, spec: dict) -> dict:
        ...

    async def delete_workload(self, workload_id: str) -> None:
        """Tear down a previously-spawned workload (``DELETE /workloads/{id}`` on the kernel).

        The COMPENSATION for a partial spawn (ROB3) and the reconcile sweeps' orphan-kill. A clean
        return means the kernel CONFIRMED the teardown (workload destroyed). Raises
        ``WorkloadUnknown`` on a 404 — the kernel does not know the workload, so termination is
        UNCONFIRMED: a container may still be live (a recreated runtime that lost its registry).
        Callers must treat that as failure-to-confirm and fail loud, never as "already gone"."""
        ...

    async def get_workload(self, workload_id: str) -> Optional[dict]:
        """The bot LIVENESS probe (``GET /workloads/{id}`` on the kernel). Returns the workload status
        ``{"workloadId": ..., "state": ...}`` while the kernel still tracks it, or ``None`` when the
        workload is GONE (404 — destroyed and reaped from the store). The reconcile sweep keys the
        active-reap on this, NOT on transcript-segment staleness: a quiet-but-live bot keeps a
        ``running`` workload, so it is never reaped on silence alone."""
        ...


class QuotaExceeded(Exception):
    """The runtime kernel rejected the spawn for owner quota (429) — surfaced as HTTP 429.

    The defense-in-depth BACKSTOP for the per-user concurrency cap: meeting-api pre-checks the cap
    (``MaxBotsExceeded``), and the kernel re-checks it via its ``owner_quota`` (this)."""


class MaxBotsExceeded(Exception):
    """meeting-api's OWN per-user concurrency pre-check rejected the spawn (P3e) — HTTP 429.

    Raised BEFORE the runtime call when the user already has ``max_concurrent`` ACTIVE bots
    (excluding infra ``browser_session``). Distinct from ``QuotaExceeded`` (the kernel's backstop),
    but both map to 429 at the route."""

    def __init__(self, user_id: int, cap: int):
        self.user_id = user_id
        self.cap = cap
        super().__init__(f"User has reached the maximum concurrent bot limit ({cap}).")


class SpawnFailed(Exception):
    """The runtime kernel could not start the workload (non-201, non-429) — meeting → failed.

    ALSO raised (ROB3) when a post-spawn DB write fails AFTER the workload was created: the orphaned
    workload is torn down (``RuntimeClient.delete_workload``) and the spawn is re-raised as this, so
    the route maps it to 502 and no inconsistent half-spawned state is left behind."""


class WorkloadUnknown(Exception):
    """The runtime answered 404 for a workload id — it does NOT know the workload.

    NOT evidence the container is gone: a runtime whose registry was lost (recreate) 404s over a
    STILL-RUNNING bot (the orphaned-live-bot incident). A delete/stop that gets this must report
    termination as UNCONFIRMED (fail loud), and the lifecycle FSM must never advance a meeting to
    a terminal state on it — only positive evidence (a tracked terminal workload state, or the
    bot's own lifecycle callback) advances the FSM."""

    def __init__(self, workload_id: str):
        self.workload_id = workload_id
        super().__init__(f"runtime does not know workload {workload_id!r} (termination unconfirmed)")


class TranscriptionNotConfigured(Exception):
    """transcribe_enabled=true but no transcription backend resolved (Settings nor env)."""


class DuplicateMeeting(Exception):
    """The user already has an active/requested meeting for (platform, native_id) → HTTP 409.

    Raised by ``MeetingRepo.create_meeting_guarded`` (the atomic dedup) — either because the in-txn
    dedup query found an active row, or because the unique partial index on active rows rejected the
    concurrent insert (the DB-level backstop). Re-exported from ``service`` for the router's mapping."""
