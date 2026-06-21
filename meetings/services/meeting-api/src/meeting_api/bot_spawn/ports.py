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

    async def count_active_bots(self, *, user_id: int, exclude_meeting_id: Optional[int] = None) -> int:
        """Count the user's ACTIVE (non-terminal) bots for the max-bots quota (P3e).

        EXCLUDES infra ``browser_session`` workloads (parent ``meetings.py:1091``
        ``Meeting.platform != "browser_session"``). The active set is
        ``{requested, joining, awaiting_admission, active}``. ``exclude_meeting_id`` lets a
        ``continue_meeting`` reopen not double-count the row it is about to reuse."""
        ...

    async def update_meeting_status(
        self,
        *,
        session_uid: str,
        status: str,
        completion_reason: Optional[str] = None,
        failure_stage: Optional[str] = None,
        data: Optional[dict] = None,
    ) -> None:
        """Persist a bot ``lifecycle.v1`` advance to the DB meeting row (the session's meeting): set
        ``status`` and merge ``completion_reason`` / ``failure_stage`` + the receiver's forensics into
        ``meeting.data`` JSONB. Maps ``session_uid`` (== the bot's ``connectionId``) → meeting via
        ``meeting_sessions``; a no-op for an unknown session (e.g. a self-host bot). So the live FSM is
        DURABLE + QUERYABLE (``GET /meetings`` reflects it, survives a restart) — not only the
        in-process ``MeetingStore`` (restored from main; the mock-bot L3 lane proved the carve dropped it)."""
        ...


@runtime_checkable
class RuntimeClient(Protocol):
    """The runtime.v1 spawn hop. ``create_workload`` POSTs a ``WorkloadSpec`` to the kernel's
    ``POST /workloads`` and returns ``{"workloadId": ..., "state": ...}`` (the parent's
    ``_spawn_via_runtime_api`` over ``POST /containers``). Raises ``QuotaExceeded`` on 429."""

    async def create_workload(self, spec: dict) -> dict:
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
    """The runtime kernel could not start the workload (non-201, non-429) — meeting → failed."""
