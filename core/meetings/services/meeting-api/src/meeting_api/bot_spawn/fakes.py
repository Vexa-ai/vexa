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

from ..meeting_writes import capture_authority_is_stale, capture_is_withdrawn
from .ports import (
    CaptureGrantConsumed,
    DuplicateMeeting,
    MaxBotsExceeded,
    QuotaExceeded,
    SpawnFailed,
    WorkloadUnknown,
)

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

    async def find_active_by_userdata(self, userdata_s3_path) -> Optional[dict]:
        for m in self._meetings.values():
            if (
                m["status"] in _ACTIVE_STATUSES
                and m.get("data", {}).get("auth_userdata_path") == userdata_s3_path
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

    async def create_meeting_guarded(
        self, *, user_id, platform, native_meeting_id, data, max_concurrent=None,
        exclude_meeting_id=None,
    ) -> dict:
        """ATOMIC dedup + cap + insert (ROB1/ROB2). The check and the insert run with NO ``await``
        between them, so even ``SlowRepo`` (which adds ``await asyncio.sleep(0)`` inside the SEPARATE
        ``count_active_bots`` / ``create_meeting`` methods) cannot interleave concurrent spawns here —
        modelling the real adapter's single-transaction guard (advisory lock + unique partial index)."""
        # 0. depleted — a cap <= 0 means NO bots allowed (0 is "depleted", never "unlimited");
        #    only ``None`` (no cap provided) skips the gate. Mirrors the real adapter.
        if max_concurrent is not None and max_concurrent <= 0:
            raise MaxBotsExceeded(user_id, max_concurrent)
        capture = data.get("zaki_capture") if isinstance(data, dict) else None
        tenant_id = capture.get("tenant_id") if isinstance(capture, dict) else None
        grant_id_sha256 = (
            capture.get("grant_id_sha256") if isinstance(capture, dict) else None
        )
        if isinstance(grant_id_sha256, str):
            for meeting in self._meetings.values():
                prior_capture = meeting["data"].get("zaki_capture")
                if (
                    meeting["user_id"] == user_id
                    and isinstance(prior_capture, dict)
                    and prior_capture.get("tenant_id") == tenant_id
                    and prior_capture.get("grant_id_sha256") == grant_id_sha256
                ):
                    raise CaptureGrantConsumed("capture authority has already been consumed")
        prior_withdrawals = [
            meeting
            for meeting in self._meetings.values()
            if meeting["user_id"] == user_id
            and meeting["platform"] == platform
            and meeting["native_meeting_id"] == native_meeting_id
            and isinstance(meeting["data"].get("zaki_capture"), dict)
            and meeting["data"]["zaki_capture"].get("tenant_id") == tenant_id
            and capture_is_withdrawn(meeting["data"])
        ]
        if prior_withdrawals and capture_authority_is_stale(
            data, max(prior_withdrawals, key=lambda meeting: meeting["id"])["data"]
        ):
            raise CaptureGrantConsumed("capture authority predates the latest withdrawal")
        # 1. dedup — an ACTIVE row for (user, platform, native) blocks the spawn (409).
        for m in self._meetings.values():
            if (
                m["user_id"] == user_id
                and m["platform"] == platform
                and m["native_meeting_id"] == native_meeting_id
                and m["status"] in _ACTIVE_STATUSES
            ):
                raise DuplicateMeeting(
                    f"An active meeting already exists for {platform}/{native_meeting_id}"
                )
        # 2. cap — count the user's ACTIVE bots (browser_session excluded); reject the N+1th (429).
        if max_concurrent is not None:
            active = sum(
                1 for m in self._meetings.values()
                if m["user_id"] == user_id
                and m["status"] in _ACTIVE_STATUSES
                and m["platform"] != "browser_session"
                and m["id"] != exclude_meeting_id
            )
            if active >= max_concurrent:
                raise MaxBotsExceeded(user_id, max_concurrent)
        # 2b. claim — a PLANNED row (intent status) for the same (user, platform, native) is
        #     UPGRADED in place, mirroring the real adapter: spawn keys merge OVER the planned
        #     data (title / scheduled_at / workspace_id / auto_join / calendar_uid survive).
        planned_rows = [
            m for m in self._meetings.values()
            if m["user_id"] == user_id
            and m["platform"] == platform
            and m["native_meeting_id"] == native_meeting_id
            and m["status"] in ("idle", "scheduled")
        ]
        if planned_rows:
            row = max(planned_rows, key=lambda m: m["id"])  # newest, like the real adapter
            row["status"] = "requested"
            row["end_time"] = None
            row["bot_container_id"] = None
            row["data"] = {**row["data"], **dict(data or {})}
            return dict(row)
        # 3. insert — NO await before this point since the dedup read, so the check+insert is atomic.
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

    async def list_scheduled_meetings(self) -> list:
        return [
            dict(m) for m in self._meetings.values()
            if m["status"] == "scheduled"
            and m["native_meeting_id"] is not None
            and m["platform"] not in (None, "", "unknown")
        ]

    async def merge_meeting_data(self, meeting_id, patch) -> None:
        m = self._meetings.get(meeting_id)
        if m is None:
            return
        for k, v in patch.items():
            if v is None:
                m["data"].pop(k, None)
            else:
                m["data"][k] = v

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

    async def mark_spawn_rejected(self, *, meeting_id, reason, data=None) -> Optional[dict]:
        row = self._meetings.get(meeting_id)
        if row is None:
            return None
        row["status"] = "failed"
        row["end_time"] = "2026-06-20T09:00:00Z"
        row["data"]["failure_stage"] = "runtime_spawn"
        row["data"]["spawn_failure_reason"] = reason
        patch = dict(data or {})
        if capture_is_withdrawn(row["data"]):
            patch.pop("zaki_capture", None)
        row["data"].update(patch)
        return dict(row)

    async def withdraw_capture(
        self, *, tenant_id, user_id, platform, native_meeting_id, withdrawn_at
    ) -> Optional[dict]:
        rows = [
            row
            for row in self._meetings.values()
            if row["user_id"] == user_id
            and row["platform"] == platform
            and row["native_meeting_id"] == native_meeting_id
            and isinstance(row["data"].get("zaki_capture"), dict)
            and row["data"]["zaki_capture"].get("tenant_id") == tenant_id
            and row["data"]["zaki_capture"].get("state") in ("authorized", "withdrawn")
        ]
        if not rows:
            return None
        row = max(rows, key=lambda item: item["id"])
        capture = row["data"].get("zaki_capture")
        if (
            not isinstance(capture, dict)
            or capture.get("tenant_id") != tenant_id
            or capture.get("state") not in ("authorized", "withdrawn")
        ):
            return None
        prior_status = row["status"]
        changed = capture.get("state") != "withdrawn"
        if changed:
            capture = dict(capture)
            capture.update(
                {
                    "state": "withdrawn",
                    "withdrawal_reason": "consent_withdrawn",
                    "withdrawn_at": withdrawn_at,
                }
            )
            row["data"]["zaki_capture"] = capture
        row["data"]["stop_requested"] = True
        should_stop = prior_status not in _TERMINAL_STATUSES
        if should_stop:
            row["status"] = "stopping"
        return {
            "meeting": dict(row),
            "changed": changed,
            "should_stop": should_stop,
            "prior_status": prior_status,
        }

    async def get_status_by_session(self, *, session_uid) -> Optional[str]:
        sess = next((s for s in self.sessions if s["session_uid"] == session_uid), None)
        if sess is None:
            return None
        row = self._meetings.get(sess["meeting_id"])
        return row["status"] if row else None

    async def find_by_container(self, *, bot_container_id) -> Optional[dict]:
        row = next(
            (m for m in self._meetings.values() if m.get("bot_container_id") == bot_container_id), None
        )
        if row is None:
            return None
        sid = next(
            (s["session_uid"] for s in reversed(self.sessions) if s["meeting_id"] == row["id"]), None
        )
        return {
            "meeting_id": row["id"],
            "status": row["status"],
            "session_uid": sid,
            "stop_requested": bool((row.get("data") or {}).get("stop_requested")),
        }

    async def update_meeting_status(
        self, *, session_uid, status, completion_reason=None, failure_stage=None, data=None
    ) -> None:
        sess = next((s for s in self.sessions if s["session_uid"] == session_uid), None)
        if sess is None:
            return  # unknown session — no-op (mirrors the SQL adapter)
        row = self._meetings.get(sess["meeting_id"])
        if row is None:
            return
        suppressed_nonterminal = (
            capture_is_withdrawn(row["data"])
            and status not in _TERMINAL_STATUSES
        )
        if suppressed_nonterminal:
            status = row["status"] if row["status"] in _TERMINAL_STATUSES else "stopping"
        row["status"] = status
        if completion_reason is not None and not suppressed_nonterminal:
            row["data"]["completion_reason"] = completion_reason
        if failure_stage is not None and not suppressed_nonterminal:
            row["data"]["failure_stage"] = failure_stage
        if not suppressed_nonterminal:
            for k, v in (data or {}).items():
                row["data"][k] = v
        return dict(row)

    async def count_active_bots(self, *, user_id, exclude_meeting_id=None) -> int:
        return sum(
            1 for m in self._meetings.values()
            if m["user_id"] == user_id
            and m["status"] in _ACTIVE_STATUSES
            and m["platform"] != "browser_session"   # infra excluded (parent meetings.py:1091)
            and m["id"] != exclude_meeting_id
        )

    async def list_stale_nonterminal(
        self, *, stop_grace: float, active_grace: float
    ) -> list:
        """In-memory mirror of the SQL adapter's general reconcile query. A row is stale once its age
        (now - ``updated_at``) passes its per-status grace (``stopping`` → stop_grace, else
        active_grace). Rows carry a static created/updated timestamp, so a test sets ``updated_at`` (or
        leaves it in the past) to mark a row stale; a row whose ``updated_at`` is recent is NOT listed."""
        from datetime import datetime, timezone

        non_terminal = {
            "requested", "joining", "awaiting_admission", "needs_help", "active", "stopping",
        }
        now = datetime.now(timezone.utc)
        out: dict = {}
        # latest session per meeting (mirror the SQL adapter's MeetingSession.id desc)
        for s in reversed(self.sessions):
            mid = s["meeting_id"]
            if mid in out:
                continue
            row = self._meetings.get(mid)
            if row is None or row["status"] not in non_terminal:
                continue
            upd = row.get("updated_at")
            try:
                u = datetime.fromisoformat(str(upd).replace("Z", "+00:00")) if upd else None
            except ValueError:
                u = None
            if u is None:
                continue
            if u.tzinfo is None:
                u = u.replace(tzinfo=timezone.utc)
            grace = stop_grace if row["status"] == "stopping" else active_grace
            if (now - u).total_seconds() < grace:
                continue
            stop_req = bool(row.get("data", {}).get("stop_requested"))
            out[mid] = (row["status"], s["session_uid"], row.get("bot_container_id"), stop_req)
        return [(mid, st, sid, bcid, sr) for mid, (st, sid, bcid, sr) in out.items()]

    # ── test affordances (not part of the port) ──────────────────────────────────────────────────
    def set_status(self, meeting_id: int, status: str) -> None:
        """Flip a meeting's status (simulate the bot reaching active / a session going terminal)."""
        self._meetings[meeting_id]["status"] = status


class FakeRuntimeClient:
    """A ``RuntimeClient`` that records the spec and returns a synthetic ``workloadId``."""

    def __init__(self, *, quota_exceeded: bool = False, fail: bool = False,
                 workloads: Optional[dict[str, dict]] = None):
        self._quota_exceeded = quota_exceeded
        self._fail = fail
        self.specs: list[dict] = []  # every spawned spec, for assertions
        self.deleted: list[str] = []  # workload ids torn down (ROB3 compensation), for assertions
        # Liveness map for the reconcile sweep: workload_id -> status dict ({"state": ...}). A workload
        # ABSENT from this map is treated as GONE (404 → None) by ``get_workload``. ``None`` defaults to
        # "every workload is alive and running" (back-compat for tests that don't care about liveness).
        self._workloads: Optional[dict[str, dict]] = workloads

    async def create_workload(self, spec: dict) -> dict[str, Any]:
        self.specs.append(spec)
        if self._quota_exceeded:
            raise QuotaExceeded("owner quota exceeded")
        if self._fail:
            raise SpawnFailed("kernel could not start the workload")
        return {"workloadId": spec["workloadId"], "state": "starting"}

    async def delete_workload(self, workload_id: str) -> None:
        # Mirrors the HTTP adapter: an id the kernel doesn't track raises WorkloadUnknown (404) —
        # termination UNCONFIRMED. With the default (no map) every teardown is tracked + confirmed.
        if self._workloads is not None and workload_id not in self._workloads:
            raise WorkloadUnknown(workload_id)
        # Record the teardown so the partial-spawn test asserts the orphaned workload was torn down.
        self.deleted.append(workload_id)
        if self._workloads is not None:
            self._workloads.pop(workload_id, None)

    async def get_workload(self, workload_id: str) -> Optional[dict[str, Any]]:
        # Default (no map injected): every workload reports alive+running, so liveness gating defers to
        # the time window only when there is NO container id. A test exercising the liveness gate injects
        # ``workloads={...}`` — a workload absent from the map is GONE (None), present is alive.
        if self._workloads is None:
            return {"workloadId": workload_id, "state": "running"}
        return self._workloads.get(workload_id)
