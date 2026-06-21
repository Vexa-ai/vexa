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
        boundary — a non-None result means ``POST /bots`` returns 409), or ``None``."""
        ...

    async def create_meeting(
        self, *, user_id: int, platform: str, native_meeting_id: str, data: dict
    ) -> dict:
        """Insert a ``Meeting`` row (status ``requested``) and return it as a dict (``id``,
        ``status``, ``created_at`` …) — the row the response is built from."""
        ...

    async def create_session(self, *, meeting_id: int, session_uid: str) -> None:
        """Eager-create the ``MeetingSession`` keyed by ``session_uid`` (== the bot's
        ``connectionId``), so a recording upload resolves its meeting before the bot reports
        ``active`` (parent ``meetings.py`` MeetingSession insert)."""
        ...

    async def set_bot_container(self, *, meeting_id: int, bot_container_id: str) -> dict:
        """Record the kernel-assigned workload id/name on the meeting and return the updated row."""
        ...


@runtime_checkable
class RuntimeClient(Protocol):
    """The runtime.v1 spawn hop. ``create_workload`` POSTs a ``WorkloadSpec`` to the kernel's
    ``POST /workloads`` and returns ``{"workloadId": ..., "state": ...}`` (the parent's
    ``_spawn_via_runtime_api`` over ``POST /containers``). Raises ``QuotaExceeded`` on 429."""

    async def create_workload(self, spec: dict) -> dict:
        ...


class QuotaExceeded(Exception):
    """The runtime kernel rejected the spawn for owner quota (429) — surfaced as HTTP 429."""


class SpawnFailed(Exception):
    """The runtime kernel could not start the workload (non-201, non-429) — meeting → failed."""
