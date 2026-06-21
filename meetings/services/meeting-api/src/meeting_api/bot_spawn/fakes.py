"""In-process fakes for the bot-spawn ports — for this module's tests (drive the SAME shipped
``request_bot`` / ``build_router`` offline, no DB, no runtime kernel).

  * ``InMemoryMeetingRepo`` — a dict-backed ``MeetingRepo``: ``create_meeting`` assigns ids and
    timestamps, ``create_session`` records (meeting_id, session_uid), ``set_bot_container`` writes
    the workload id back. ``sessions`` is exposed so a test asserts the eager session was created.
  * ``FakeRuntimeClient`` — a ``RuntimeClient`` that records the spec it was asked to spawn and
    returns a synthetic ``workloadId``. Construct with ``quota_exceeded=True`` / ``fail=True`` to
    exercise the 429 / spawn-failed seams.

NO production logic — they only stand in for Postgres + the runtime kernel so the spawn flow runs
fully in-process.
"""
from __future__ import annotations

from typing import Any, Optional

from .ports import QuotaExceeded, SpawnFailed


class InMemoryMeetingRepo:
    """A dict-backed ``MeetingRepo`` keyed by the synthetic meeting id."""

    def __init__(self):
        self._meetings: dict[int, dict] = {}
        self._next_id = 1
        self.sessions: list[dict] = []  # exposed for assertions

    async def find_active(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        for m in self._meetings.values():
            if (
                m["user_id"] == user_id
                and m["platform"] == platform
                and m["native_meeting_id"] == native_meeting_id
                and m["status"] in ("requested", "joining", "awaiting_admission", "active")
            ):
                return dict(m)
        return None

    async def create_meeting(self, *, user_id, platform, native_meeting_id, data) -> dict:
        mid = self._next_id
        self._next_id += 1
        row = {
            "id": mid,
            "user_id": user_id,
            "platform": platform,
            "native_meeting_id": native_meeting_id,
            "platform_specific_id": native_meeting_id,
            "status": "requested",
            "bot_container_id": None,
            "start_time": None,
            "end_time": None,
            "data": dict(data or {}),
            "created_at": "2026-06-20T09:00:00Z",
            "updated_at": "2026-06-20T09:00:00Z",
        }
        self._meetings[mid] = row
        return dict(row)

    async def create_session(self, *, meeting_id, session_uid) -> None:
        self.sessions.append({"meeting_id": meeting_id, "session_uid": session_uid})

    async def set_bot_container(self, *, meeting_id, bot_container_id) -> dict:
        row = self._meetings[meeting_id]
        row["bot_container_id"] = bot_container_id
        return dict(row)


class FakeRuntimeClient:
    """A ``RuntimeClient`` that records the spec and returns a synthetic ``workloadId``."""

    def __init__(self, *, quota_exceeded: bool = False, fail: bool = False):
        self._quota_exceeded = quota_exceeded
        self._fail = fail
        self.specs: list[dict] = []  # every spawned spec, for assertions

    async def create_workload(self, spec: dict) -> dict[str, Any]:
        self.specs.append(spec)
        if self._quota_exceeded:
            raise QuotaExceeded("owner quota exceeded")
        if self._fail:
            raise SpawnFailed("kernel could not start the workload")
        return {"workloadId": spec["workloadId"], "state": "starting"}
