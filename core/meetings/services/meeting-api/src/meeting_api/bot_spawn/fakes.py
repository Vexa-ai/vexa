"""In-process fakes for the bot-spawn ports — for this module's tests (drive the SAME shipped
``request_bot`` / ``build_router`` offline, no DB, no runtime kernel).

  * ``InMemoryMeetingRepo`` — a dict-backed ``MeetingRepo``: ``create_meeting`` assigns ids and
    timestamps, ``create_session`` records (meeting_id, session_uid), ``set_bot_container`` writes
    the workload id back. N sessions accumulate per meeting; ``continue_meeting`` reuses a terminal
    row + appends a session; ``count_active_bots`` powers the max-bots quota (browser_session
    excluded). ``sessions`` is exposed so a test asserts sessions were created. A test can flip a
    meeting's ``status`` directly to simulate the bot reaching active / a session going terminal.
  * ``FakeRuntimeClient`` — a ``RuntimeClient`` that records the spec it was asked to spawn and
    returns a synthetic ``workloadId``. Construct with ``quota_exceeded=True`` / ``fail=True`` to
    exercise the 429 / spawn-failed seams.

NO production logic — they only stand in for Postgres + the runtime kernel so the spawn flow runs
fully in-process.
"""
from __future__ import annotations

from typing import Any, Optional

from .ports import QuotaExceeded, SpawnFailed

_ACTIVE_STATUSES = ("requested", "joining", "awaiting_admission", "active")
_TERMINAL_STATUSES = ("completed", "failed")


class InMemoryMeetingRepo:
    """A dict-backed ``MeetingRepo`` keyed by the synthetic meeting id."""

    def __init__(self):
        self._meetings: dict[int, dict] = {}
        self._next_id = 1
        self.sessions: list[dict] = []  # exposed for assertions (all sessions, all meetings)
        self.reopened: list[int] = []   # meeting ids continue_meeting reused

    async def find_active(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        for m in self._meetings.values():
            if (
                m["user_id"] == user_id
                and m["platform"] == platform
                and m["native_meeting_id"] == native_meeting_id
                and m["status"] in _ACTIVE_STATUSES
            ):
                return dict(m)
        return None

    async def find_latest(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        rows = [
            m for m in self._meetings.values()
            if m["user_id"] == user_id
            and m["platform"] == platform
            and m["native_meeting_id"] == native_meeting_id
        ]
        if not rows:
            return None
        return dict(max(rows, key=lambda m: m["id"]))  # id is monotonic → most recent

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

    async def reopen_meeting(self, *, meeting_id) -> dict:
        row = self._meetings[meeting_id]
        row["status"] = "requested"
        row["end_time"] = None
        row["bot_container_id"] = None
        # Clear the prior terminal attribution but KEEP the row + its transcripts/recordings.
        for k in ("completion_reason", "failure_stage"):
            row["data"].pop(k, None)
        self.reopened.append(meeting_id)
        return dict(row)

    async def create_session(self, *, meeting_id, session_uid) -> None:
        self.sessions.append({"meeting_id": meeting_id, "session_uid": session_uid})

    async def list_sessions(self, *, meeting_id) -> list:
        return [s["session_uid"] for s in self.sessions if s["meeting_id"] == meeting_id]

    async def set_bot_container(self, *, meeting_id, bot_container_id) -> dict:
        row = self._meetings[meeting_id]
        row["bot_container_id"] = bot_container_id
        return dict(row)

    async def count_active_bots(self, *, user_id, exclude_meeting_id=None) -> int:
        return sum(
            1 for m in self._meetings.values()
            if m["user_id"] == user_id
            and m["status"] in _ACTIVE_STATUSES
            and m["platform"] != "browser_session"   # infra excluded (parent meetings.py:1091)
            and m["id"] != exclude_meeting_id
        )

    # ── test affordances (not part of the port) ──────────────────────────────────────────────────
    def set_status(self, meeting_id: int, status: str) -> None:
        """Flip a meeting's status (simulate the bot reaching active / a session going terminal)."""
        self._meetings[meeting_id]["status"] = status


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
