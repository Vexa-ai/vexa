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

from ..lifecycle.machine import dominant_completion_reason
from .ports import (
    DuplicateMeeting,
    MaxBotsExceeded,
    MeetingStopped,
    QuotaExceeded,
    SpawnFailed,
    WorkloadUnknown,
    _archive_completion,
    _stopped_reopen_detail,
    reconcile_grace_for_status,
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

    async def find_active_rows(self, user_id, platform, native_meeting_id) -> list:
        rows = [
            dict(m) for m in self._meetings.values()
            if m["user_id"] == user_id
            and m["platform"] == platform
            and m["native_meeting_id"] == native_meeting_id
            and m["status"] not in ("completed", "failed")
        ]
        rows.sort(key=lambda m: m.get("id") or 0, reverse=True)   # newest first, as the SQL does
        return rows

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
            planned = dict(row["data"])
            # A THIS-REQUEST dispatch supersedes an earlier stop ON THE PLAN. Legacy zombie rows
            # exist (a scheduled row flagged by a rev-193 DELETE that never terminalized it), and
            # claiming one while the flag rides along would make the spawn fence abort the very bot
            # the user just asked for. The flag records intent about the run that WAS planned; this
            # is a new one. (Post-fix, a stop terminalizes the planned row, so it is not claimable
            # at all — this only ever meets rows written by an older build.)
            planned.pop("stop_requested", None)
            row["data"] = {**planned, **dict(data or {})}
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

    async def list_live_meetings(self) -> list:
        from .auto_join import LIVE_STATUSES

        return [
            dict(m) for m in self._meetings.values()
            if m["status"] in LIVE_STATUSES
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

    async def get_meeting(self, meeting_id) -> Optional[dict]:
        row = self._meetings.get(meeting_id)
        # A COPY, like every other read: the spawn fence must observe committed row state, never
        # alias the live dict (which would make the fence pass trivially in-process and hide the
        # very race it exists to close).
        return dict(row) if row else None

    async def reopen_meeting(self, *, meeting_id, data_patch=None) -> dict:
        row = self._meetings[meeting_id]
        data = row.get("data") or {}
        if data.get("stop_requested"):
            raise MeetingStopped(_stopped_reopen_detail(meeting_id))
        deletion_pending = data.get("artifact_deletion") or any(
            r.get("deletion_pending") for r in (data.get("recordings") or [])
            if isinstance(r, dict)
        )
        if row.get("status") not in ("completed", "failed") or deletion_pending:
            raise DuplicateMeeting("Terminal meeting is no longer reusable")
        row["status"] = "requested"
        row["end_time"] = None
        row["bot_container_id"] = None
        # KEEP the row + its transcripts/recordings, and keep the prior run's ENDING too — archived,
        # not erased (the SAME helper the SQL adapter uses).
        _archive_completion(row["data"])
        for key, value in (data_patch or {}).items():
            if value is None:
                row["data"].pop(key, None)
            else:
                row["data"][key] = value
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

    async def fail_meeting(
        self, *, meeting_id, reason, failure_stage="requested",
        completion_reason="start_failed", data=None,
    ) -> Optional[dict]:
        row = self._meetings.get(meeting_id)
        if row is None:
            return None
        row["status"] = "failed"
        row["data"].update(dict(data or {}))
        if failure_stage is not None:
            row["data"]["failure_stage"] = failure_stage
        row["data"]["failure_reason"] = reason
        row["data"]["completion_reason"] = dominant_completion_reason(
            completion_reason, stop_requested=bool(row["data"].get("stop_requested"))
        )
        return dict(row)

    async def get_status_by_session(self, *, session_uid) -> Optional[str]:
        sess = next((s for s in self.sessions if s["session_uid"] == session_uid), None)
        if sess is None:
            return None
        row = self._meetings.get(sess["meeting_id"])
        return row["status"] if row else None

    async def get_lifecycle_state_by_session(self, *, session_uid) -> Optional[dict]:
        sess = next((s for s in self.sessions if s["session_uid"] == session_uid), None)
        if sess is None:
            return None
        row = self._meetings.get(sess["meeting_id"])
        if row is None:
            return None
        return {
            "status": row["status"],
            "data": dict(row.get("data") or {}),
        }

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
        row["status"] = status
        if completion_reason is not None:
            row["data"]["completion_reason"] = completion_reason
        if failure_stage is not None:
            row["data"]["failure_stage"] = failure_stage
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

    async def list_service_authority_sessions(self) -> list[dict]:
        """Active rows carrying a per-run authority identity."""
        return [
            dict(m)
            for m in self._meetings.values()
            if m["status"] in ("active", "needs_help")
            and isinstance(m.get("data", {}).get("service_authority"), dict)
            and m["data"]["service_authority"].get("mode")
            in ("enforce", "observe")
        ]

    async def record_service_authority_decision(
        self,
        *,
        meeting_id,
        request,
        decision,
    ) -> bool:
        row = self._meetings.get(meeting_id)
        if row is None:
            return False
        metadata = row.get("data", {}).get("service_authority")
        if (
            not isinstance(metadata, dict)
            or metadata.get("service_identity")
            != request.service_identity
        ):
            return False
        boundary = request.boundary_at.isoformat()
        if metadata.get("last_boundary_at") == boundary:
            if metadata.get("last_decision_id") != decision.decision_id:
                raise ValueError(
                    "service-authority boundary decision conflicts"
                )
            return False
        if metadata.get("last_boundary_at"):
            from datetime import datetime

            previous = datetime.fromisoformat(
                metadata["last_boundary_at"].replace("Z", "+00:00")
            )
            if previous >= request.boundary_at:
                return False
        metadata.update(decision.to_record())
        metadata["last_boundary_at"] = boundary
        metadata["last_decision_id"] = decision.decision_id
        if (
            decision.enforced
            and not decision.allow
            and decision.stop_scope == "billable_service"
        ):
            metadata["teardown_confirmed"] = False
            row["data"]["stop_requested"] = True
            row["status"] = "stopping"
        return True

    async def list_service_authority_teardowns(self) -> list[dict]:
        out = []
        for row in self._meetings.values():
            metadata = row.get("data", {}).get("service_authority")
            if (
                isinstance(metadata, dict)
                and metadata.get("enforced") is True
                and metadata.get("allow") is False
                and metadata.get("stop_scope") == "billable_service"
                and metadata.get("teardown_confirmed") is not True
            ):
                out.append({
                    "id": row["id"],
                    "bot_container_id": row.get("bot_container_id"),
                    "decision_id": metadata.get("decision_id"),
                })
        return out

    async def claim_service_authority_teardown(
        self,
        *,
        meeting_id,
        claim_id,
        claimed_at,
        lease_seconds,
    ) -> Optional[dict]:
        from datetime import datetime, timezone

        row = self._meetings.get(meeting_id)
        metadata = (
            row.get("data", {}).get("service_authority")
            if row is not None
            else None
        )
        if (
            not isinstance(metadata, dict)
            or metadata.get("enforced") is not True
            or metadata.get("allow") is not False
            or metadata.get("stop_scope") != "billable_service"
            or metadata.get("teardown_confirmed") is True
        ):
            return None
        prior_claim = metadata.get("teardown_claim_id")
        prior_at = metadata.get("teardown_claimed_at")
        if prior_claim and prior_at:
            try:
                prior_time = datetime.fromisoformat(
                    prior_at.replace("Z", "+00:00"),
                ).astimezone(timezone.utc)
            except (TypeError, ValueError):
                return None
            if (claimed_at - prior_time).total_seconds() < lease_seconds:
                return None
        metadata["teardown_claim_id"] = claim_id
        metadata["teardown_claimed_at"] = claimed_at.isoformat()
        return {
            "id": row["id"],
            "bot_container_id": row.get("bot_container_id"),
            "decision_id": metadata.get("decision_id"),
            "claim_id": claim_id,
        }

    async def confirm_service_authority_teardown(
        self,
        *,
        meeting_id,
        decision_id,
        claim_id,
    ) -> bool:
        row = self._meetings.get(meeting_id)
        metadata = (
            row.get("data", {}).get("service_authority")
            if row is not None
            else None
        )
        if (
            not isinstance(metadata, dict)
            or metadata.get("decision_id") != decision_id
            or metadata.get("teardown_claim_id") != claim_id
            or metadata.get("teardown_confirmed") is True
        ):
            return False
        metadata["teardown_confirmed"] = True
        metadata["teardown_claim_id"] = None
        metadata["teardown_claimed_at"] = None
        return True

    async def list_stale_nonterminal(
        self, *, stop_grace: float, active_grace: float, preactive_grace: Optional[float] = None
    ) -> list:
        """In-memory mirror of the SQL adapter's general reconcile query. A row is stale once its age
        (now - ``updated_at``) passes its per-status grace (``reconcile_grace_for_status`` — the SAME
        policy the SQL adapter reads, so the two listings cannot drift). Rows carry a static created/
        updated timestamp, so a test sets ``updated_at`` (or leaves it in the past) to mark a row
        stale; a row whose ``updated_at`` is recent is NOT listed."""
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
            grace = reconcile_grace_for_status(
                row["status"], stop_grace, active_grace, preactive_grace
            )
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
                 dead_on_arrival: bool = False,
                 workloads: Optional[dict[str, dict]] = None):
        self._quota_exceeded = quota_exceeded
        self._fail = fail
        # dead_on_arrival models a kernel that (against #718 C1) still answers 201 but with a workload
        # that never started (state=stopped/start_failed). The HTTP adapter's body-state check (C2)
        # must catch it, so the FAKE returns that shape verbatim rather than raising — the belt of the
        # belt-and-suspenders defense the adapter owns.
        self._dead_on_arrival = dead_on_arrival
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
        if self._dead_on_arrival:
            return {"workloadId": spec["workloadId"], "state": "stopped", "stopReason": "start_failed"}
        return {"workloadId": spec["workloadId"], "state": "starting"}

    async def delete_workload(self, workload_id: str) -> None:
        # Mirrors the HTTP adapter: an id the kernel doesn't track raises WorkloadUnknown (404).
        # Absence is NOT evidence the underlying workload stopped; every caller must preserve that
        # distinction until it has a positive destroyed/teardown observation.
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
