"""Production adapters — the real ``MeetingRepo`` (SQLAlchemy) + ``RuntimeClient`` (runtime.v1 HTTP).

Thin translations of the ports to the concrete clients, exactly as the parent's
``meetings.request_bot`` did (SQLAlchemy INSERTs for the meeting + session; an httpx POST to the
runtime kernel's ``POST /workloads``). They carry NO test logic.

Heavy imports (SQLAlchemy, httpx) are LAZY (inside the methods / ``build_production_router``) so the
package can be imported and unit-tested with the in-memory fakes without those runtime deps in the
gate venv — which is why ``pyproject.toml`` needs no ``greenlet`` pin.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from ..meeting_writes import (
    MEETING_WRITE_LOCK_NAMESPACE,
    capture_authority_is_stale,
    capture_is_withdrawn,
)
from ..sessions import new_session
from .ports import (
    CaptureGrantConsumed,
    DuplicateMeeting,
    MaxBotsExceeded,
    QuotaExceeded,
    SpawnFailed,
    WorkloadUnknown,
)


def _row_to_dict(m) -> dict:
    return {
        "id": m.id,
        "user_id": m.user_id,
        "platform": m.platform,
        "native_meeting_id": m.platform_specific_id,
        "platform_specific_id": m.platform_specific_id,
        "status": m.status,
        "bot_container_id": m.bot_container_id,
        "start_time": m.start_time.isoformat() if m.start_time else None,
        "end_time": m.end_time.isoformat() if m.end_time else None,
        "data": m.data if isinstance(m.data, dict) else {},
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


class SqlAlchemyMeetingRepo:
    """``MeetingRepo`` over a SQLAlchemy-async ``session_factory`` (``meetings`` /
    ``meeting_sessions`` tables). Carve of the parent ``meetings.request_bot`` DB ops."""

    def __init__(self, session_factory, *, statement_factory=None):
        self._session_factory = session_factory
        self._statement_factory = statement_factory

    def _statement(self, sql: str):
        if self._statement_factory is not None:
            return self._statement_factory(sql)
        from sqlalchemy import text

        return text(sql)

    async def find_active(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        from sqlalchemy import select

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            stmt = (
                select(Meeting)
                .where(
                    Meeting.user_id == user_id,
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                    Meeting.status.in_(["requested", "joining", "awaiting_admission", "active"]),
                )
                .order_by(Meeting.created_at.desc())
            )
            m = (await db.execute(stmt)).scalars().first()
            return _row_to_dict(m) if m else None

    async def find_latest(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        from sqlalchemy import select

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            stmt = (
                select(Meeting)
                .where(
                    Meeting.user_id == user_id,
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                )
                .order_by(Meeting.created_at.desc(), Meeting.id.desc())
            )
            m = (await db.execute(stmt)).scalars().first()
            return _row_to_dict(m) if m else None

    async def reopen_meeting(self, *, meeting_id) -> dict:
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            m = (
                await db.execute(select(Meeting).where(Meeting.id == meeting_id))
            ).scalars().first()
            m.status = "requested"
            m.end_time = None
            m.bot_container_id = None
            data = dict(m.data) if isinstance(m.data, dict) else {}
            for k in ("completion_reason", "failure_stage"):
                data.pop(k, None)
            m.data = data
            flag_modified(m, "data")
            # updated_at is set server-side by the column's onupdate=func.now() (main's pattern);
            # never write a tz-aware Python datetime into the naive column (asyncpg DataError).
            await db.commit()
            await db.refresh(m)
            return _row_to_dict(m)

    async def get_status_by_session(self, *, session_uid) -> Optional[str]:
        from sqlalchemy import select

        from ..sessions.models import Meeting, MeetingSession

        async with self._session_factory() as db:
            sess = (
                await db.execute(select(MeetingSession).where(MeetingSession.session_uid == session_uid))
            ).scalars().first()
            if sess is None:
                return None
            status = (
                await db.execute(select(Meeting.status).where(Meeting.id == sess.meeting_id))
            ).scalars().first()
            return status

    async def find_by_container(self, *, bot_container_id) -> Optional[dict]:
        """The meeting + latest session for a workload id — used by the runtime callback (CC5) to drive a
        synthetic ``failed`` for a workload that died before the bot reported. ``{meeting_id, status,
        session_uid}`` or ``None``."""
        from sqlalchemy import select

        from ..sessions.models import Meeting, MeetingSession

        async with self._session_factory() as db:
            row = (
                await db.execute(
                    select(Meeting.id, Meeting.status).where(Meeting.bot_container_id == bot_container_id)
                )
            ).first()
            if row is None:
                return None
            mid, status = row
            sid = (
                await db.execute(
                    select(MeetingSession.session_uid)
                    .where(MeetingSession.meeting_id == mid)
                    .order_by(MeetingSession.id.desc())
                )
            ).scalars().first()
            return {"meeting_id": mid, "status": status, "session_uid": sid}

    async def update_meeting_status(
        self, *, session_uid, status, completion_reason=None, failure_stage=None, data=None
    ) -> None:
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from ..sessions.models import Meeting, MeetingSession

        async with self._session_factory() as db:
            sess = (
                await db.execute(select(MeetingSession).where(MeetingSession.session_uid == session_uid))
            ).scalars().first()
            if sess is None:
                return  # unknown session (e.g. a self-host bot) — nothing to persist
            m = (
                await db.execute(select(Meeting).where(Meeting.id == sess.meeting_id).with_for_update())
                # FOR UPDATE: db-writer/recordings/docs all lock before read-modify-write of data
                # JSONB; without it a concurrent db-writer merge commit is clobbered (#53 review).
            ).scalars().first()
            if m is None:
                return
            merged = dict(m.data) if isinstance(m.data, dict) else {}
            suppressed_nonterminal = (
                capture_is_withdrawn(merged)
                and status not in ("completed", "failed")
            )
            if suppressed_nonterminal:
                status = m.status if m.status in ("completed", "failed") else "stopping"
            m.status = status
            if completion_reason is not None and not suppressed_nonterminal:
                merged["completion_reason"] = completion_reason
            if failure_stage is not None and not suppressed_nonterminal:
                merged["failure_stage"] = failure_stage
            if not suppressed_nonterminal:
                for k, v in (data or {}).items():
                    merged[k] = v
            m.data = merged
            flag_modified(m, "data")
            # Naive UTC into the naive time columns (tz-aware → asyncpg DataError, per set_bot_container).
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if status == "active" and m.start_time is None:
                m.start_time = now
            if status in ("completed", "failed") and m.end_time is None:
                m.end_time = now
            await db.commit()
            # Refresh BEFORE _row_to_dict: `updated_at` has a server-side onupdate, so it is expired
            # post-commit; reading it in _row_to_dict would trigger implicit async IO (MissingGreenlet).
            # The other write adapters (create_meeting/set_bot_container/reopen) follow the same pattern.
            await db.refresh(m)
            # Return the updated row so the lifecycle callback can deliver the per-user webhook from
            # meeting.data (and the stop route gets a clean dict) without a second query.
            return _row_to_dict(m)

    async def count_active_bots(self, *, user_id, exclude_meeting_id=None) -> int:
        from sqlalchemy import func, select

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            stmt = (
                select(func.count())
                .select_from(Meeting)
                .where(
                    Meeting.user_id == user_id,
                    Meeting.status.in_(["requested", "joining", "awaiting_admission", "active"]),
                    Meeting.platform != "browser_session",  # infra excluded (parent meetings.py:1091)
                )
            )
            if exclude_meeting_id is not None:
                stmt = stmt.where(Meeting.id != exclude_meeting_id)
            return int((await db.execute(stmt)).scalar() or 0)

    async def list_stale_stopping(
        self, *, older_than_seconds: float
    ) -> list[tuple[int, str, Optional[str]]]:
        """Meetings stuck in ``stopping`` longer than ``older_than_seconds`` — with their latest
        session_uid AND ``bot_container_id``. The stop-reconcile backstop completes these (the bot was
        told to leave but never sent its own terminal callback) AND kills the workload (CC6), since an
        ACTIVE bot that missed the fire-and-forget leave is an orphan until torn down. Returns
        ``[(meeting_id, session_uid, bot_container_id), …]`` (bot_container_id may be ``None``)."""
        from datetime import datetime, timezone

        from sqlalchemy import select

        from ..sessions.models import Meeting, MeetingSession

        async with self._session_factory() as db:
            rows = (
                await db.execute(
                    select(Meeting.id, Meeting.updated_at, MeetingSession.session_uid,
                           Meeting.bot_container_id)
                    .join(MeetingSession, MeetingSession.meeting_id == Meeting.id)
                    .where(Meeting.status == "stopping")
                    .order_by(MeetingSession.id.desc())
                )
            ).all()
        now = datetime.now(timezone.utc)
        out: dict[int, tuple[str, Optional[str]]] = {}
        for mid, upd, sid, bcid in rows:
            if mid in out or upd is None or not sid:
                continue
            u = upd if upd.tzinfo else upd.replace(tzinfo=timezone.utc)
            if (now - u).total_seconds() >= older_than_seconds:
                out[mid] = (sid, bcid)
        return [(mid, sid, bcid) for mid, (sid, bcid) in out.items()]

    async def list_stale_nonterminal(
        self, *, stop_grace: float, active_grace: float
    ) -> list[tuple[int, str, str, Optional[str], bool]]:
        """Meetings stuck in ANY non-terminal status whose row has gone quiet past its grace window —
        a bot that exited (or vanished) without ever sending its terminal lifecycle callback leaves the
        row hung here forever. ``updated_at`` is bumped on every status change AND on segment/heartbeat
        persistence. NOTE: for a LIVE status (`active`/`needs_help`) ``updated_at`` staleness is a
        CANDIDATE signal only — the sweep additionally gates the active-reap on runtime workload
        liveness (see ``reconcile.py``), because a silent-but-live bot stops bumping ``updated_at``.

        Per-row window: ``stopping`` uses ``stop_grace`` (a stop was requested — clear it fast),
        everything else uses ``active_grace`` (a longer idle so a momentarily-quiet live bot is not
        reaped). Returns ``[(meeting_id, status, session_uid, bot_container_id, stop_requested), …]`` with
        the LATEST session_uid per meeting (mirrors ``list_stale_stopping``)."""
        from datetime import datetime, timezone

        from sqlalchemy import select

        from ..sessions.models import Meeting, MeetingSession

        non_terminal = [
            "requested", "joining", "awaiting_admission", "needs_help", "active", "stopping",
        ]
        async with self._session_factory() as db:
            rows = (
                await db.execute(
                    select(Meeting.id, Meeting.status, Meeting.updated_at,
                           MeetingSession.session_uid, Meeting.bot_container_id, Meeting.data)
                    .join(MeetingSession, MeetingSession.meeting_id == Meeting.id)
                    .where(Meeting.status.in_(non_terminal))
                    .order_by(MeetingSession.id.desc())
                )
            ).all()
        now = datetime.now(timezone.utc)
        out: dict[int, tuple[str, str, Optional[str], bool]] = {}
        for mid, status, upd, sid, bcid, data in rows:
            if mid in out or upd is None or not sid:
                continue
            u = upd if upd.tzinfo else upd.replace(tzinfo=timezone.utc)
            grace = stop_grace if status == "stopping" else active_grace
            if (now - u).total_seconds() >= grace:
                stop_req = bool(isinstance(data, dict) and data.get("stop_requested"))
                out[mid] = (status, sid, bcid, stop_req)
        return [(mid, st, sid, bcid, sr) for mid, (st, sid, bcid, sr) in out.items()]

    async def create_meeting(self, *, user_id, platform, native_meeting_id, data) -> dict:
        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            m = Meeting(
                user_id=user_id, platform=platform, platform_specific_id=native_meeting_id,
                status="requested", data=dict(data or {}),
            )
            db.add(m)
            await db.commit()
            await db.refresh(m)
            return _row_to_dict(m)

    async def create_meeting_guarded(
        self, *, user_id, platform, native_meeting_id, data, max_concurrent=None,
        exclude_meeting_id=None,
    ) -> dict:
        """ATOMIC dedup + cap + insert in ONE transaction (ROB1/ROB2).

        The TOCTOU-safe spawn primitive. Two layers guard it:

          * a per-user ``pg_advisory_xact_lock(:user_id)`` taken as the FIRST statement so concurrent
            spawns for the SAME user SERIALIZE through this txn (the lock auto-releases at commit/
            rollback). With the lock held, the dedup query + cap COUNT + INSERT see a stable snapshot.
          * a unique partial index on active rows (``uq_meeting_active_user_platform_native`` — see
            sessions/models.py) as the DB-level backstop: if a racing transaction (or a different
            meeting-api process not covered by THIS advisory lock) inserted a duplicate active row, the
            INSERT's commit raises ``IntegrityError`` → mapped to ``DuplicateMeeting``.
        """
        # 0. depleted — a cap <= 0 means NO bots allowed (0 is "depleted", never "unlimited");
        #    reject before touching the DB. Only ``None`` (no cap provided) skips the gate.
        if max_concurrent is not None and max_concurrent <= 0:
            raise MaxBotsExceeded(user_id, max_concurrent)

        active = ["requested", "joining", "awaiting_admission", "active"]
        async with self._session_factory() as db:
            # Per-user serialization: hold the advisory lock for the whole transaction.
            await db.execute(
                self._statement("SELECT pg_advisory_xact_lock(:uid)"),
                {"uid": user_id},
            )
            capture = data.get("zaki_capture") if isinstance(data, dict) else None
            tenant_id = capture.get("tenant_id") if isinstance(capture, dict) else None
            grant_id_sha256 = (
                capture.get("grant_id_sha256") if isinstance(capture, dict) else None
            )
            if isinstance(grant_id_sha256, str):
                prior_withdrawal = (
                    await db.execute(
                        self._statement(
                            "SELECT data->'zaki_capture' AS zaki_capture FROM meetings "
                            "WHERE user_id = :user_id AND platform = :platform "
                            "AND platform_specific_id = :native_meeting_id "
                            "AND data->'zaki_capture'->>'tenant_id' = :tenant_id "
                            "AND data->'zaki_capture'->>'state' = 'withdrawn' "
                            "ORDER BY created_at DESC, id DESC LIMIT 1"
                        ),
                        {
                            "user_id": user_id,
                            "platform": platform,
                            "native_meeting_id": native_meeting_id,
                            "tenant_id": tenant_id,
                        },
                    )
                ).mappings().first()
                if prior_withdrawal is not None and capture_authority_is_stale(
                    data, prior_withdrawal
                ):
                    raise CaptureGrantConsumed(
                        "capture authority predates the latest withdrawal"
                    )
            from sqlalchemy import func, select
            from sqlalchemy.exc import IntegrityError

            from ..sessions.models import Meeting

            if isinstance(grant_id_sha256, str):
                consumed = (
                    await db.execute(
                        select(Meeting.id)
                        .where(
                            Meeting.user_id == user_id,
                            Meeting.data["zaki_capture"]["tenant_id"].astext == tenant_id,
                            Meeting.data["zaki_capture"]["grant_id_sha256"].astext
                            == grant_id_sha256,
                        )
                        .limit(1)
                    )
                ).scalar()
                if consumed is not None:
                    raise CaptureGrantConsumed("capture authority has already been consumed")
            # 1. dedup — under the lock, an active row for (user, platform, native) blocks the spawn.
            dup = (
                await db.execute(
                    select(Meeting.id).where(
                        Meeting.user_id == user_id,
                        Meeting.platform == platform,
                        Meeting.platform_specific_id == native_meeting_id,
                        Meeting.status.in_(active),
                    )
                )
            ).scalars().first()
            if dup is not None:
                raise DuplicateMeeting(
                    f"An active meeting already exists for {platform}/{native_meeting_id}"
                )
            # 2. cap — count the user's active bots (browser_session excluded); reject the N+1th.
            #    (cap <= 0 was already rejected as depleted above.)
            if max_concurrent is not None:
                count_stmt = (
                    select(func.count())
                    .select_from(Meeting)
                    .where(
                        Meeting.user_id == user_id,
                        Meeting.status.in_(active),
                        Meeting.platform != "browser_session",
                    )
                )
                if exclude_meeting_id is not None:
                    count_stmt = count_stmt.where(Meeting.id != exclude_meeting_id)
                n_active = int((await db.execute(count_stmt)).scalar() or 0)
                if n_active >= max_concurrent:
                    raise MaxBotsExceeded(user_id, max_concurrent)
            # 2b. claim — a PLANNED row (intent status `idle`/`scheduled`, created by POST /meetings
            #     or calendar sync) for the SAME (user, platform, native) is UPGRADED in place instead
            #     of inserting a second row: without this, the unique partial index (which covers
            #     intent statuses too) would 409 the spawn. The planned analog of ``reopen_meeting``,
            #     atomic under the same advisory lock. Spawn keys merge OVER the planned data; the
            #     plan's `title` / `scheduled_at` / `workspace_id` / `auto_join` / `calendar_uid`
            #     survive — the plan, its workspace bind, and the transcript live on ONE row.
            from sqlalchemy.orm.attributes import flag_modified

            claimable = (await db.execute(
                select(Meeting).where(
                    Meeting.user_id == user_id,
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                    Meeting.status.in_(("idle", "scheduled")),
                ).order_by(Meeting.created_at.desc()).limit(1).with_for_update()
            )).scalars().first()
            if claimable is not None:
                planned = dict(claimable.data) if isinstance(claimable.data, dict) else {}
                claimable.status = "requested"
                claimable.end_time = None
                claimable.bot_container_id = None
                claimable.data = {**planned, **dict(data or {})}
                flag_modified(claimable, "data")
                await db.commit()
                await db.refresh(claimable)
                return _row_to_dict(claimable)
            # 3. insert — still inside the same txn/lock, so check+insert is atomic.
            m = Meeting(
                user_id=user_id, platform=platform, platform_specific_id=native_meeting_id,
                status="requested", data=dict(data or {}),
            )
            db.add(m)
            try:
                await db.commit()
            except IntegrityError as e:
                # The unique partial index backstop fired — a concurrent duplicate active row won the
                # race (e.g. a spawn in another process the advisory lock didn't cover). Treat as dedup.
                await db.rollback()
                raise DuplicateMeeting(
                    f"An active meeting already exists for {platform}/{native_meeting_id}"
                ) from e
            await db.refresh(m)
            return _row_to_dict(m)

    async def list_scheduled_meetings(self) -> list[dict]:
        """Every ``scheduled`` row with a joinable link (the auto-join sweep's candidate set —
        the time/toggle/backoff filtering is the sweep's pure ``due_rows``)."""
        from sqlalchemy import select

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            rows = (await db.execute(
                select(Meeting).where(
                    Meeting.status == "scheduled",
                    Meeting.platform_specific_id.isnot(None),
                    Meeting.platform != "unknown",
                )
            )).scalars().all()
            return [_row_to_dict(m) for m in rows]

    async def merge_meeting_data(self, meeting_id, patch: dict) -> None:
        """Merge ``patch`` into ``meeting.data`` (a ``None`` value REMOVES the key) — the sweep's
        error/backoff stamping primitive. Row-locked; a missing row is a no-op."""
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            meeting = (await db.execute(
                select(Meeting).where(Meeting.id == meeting_id).with_for_update()
            )).scalars().first()
            if meeting is None:
                return
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            for k, v in patch.items():
                if v is None:
                    data.pop(k, None)
                else:
                    data[k] = v
            meeting.data = data
            flag_modified(meeting, "data")
            await db.commit()

    async def create_session(self, *, meeting_id, session_uid) -> None:
        async with self._session_factory() as db:
            db.add(new_session(meeting_id, session_uid))
            await db.commit()

    async def list_sessions(self, *, meeting_id) -> list:
        from sqlalchemy import select

        from ..sessions.models import MeetingSession

        async with self._session_factory() as db:
            stmt = (
                select(MeetingSession.session_uid)
                .where(MeetingSession.meeting_id == meeting_id)
                .order_by(MeetingSession.session_start_time.asc(), MeetingSession.id.asc())
            )
            return [r for (r,) in (await db.execute(stmt)).all()]

    async def set_bot_container(self, *, meeting_id, bot_container_id) -> dict:
        from sqlalchemy import select

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            m = (
                await db.execute(select(Meeting).where(Meeting.id == meeting_id))
            ).scalars().first()
            m.bot_container_id = bot_container_id
            # updated_at is set server-side by the column's onupdate=func.now() (main's pattern);
            # never write a tz-aware Python datetime into the naive column (asyncpg DataError).
            await db.commit()
            await db.refresh(m)
            return _row_to_dict(m)

    async def mark_spawn_rejected(self, *, meeting_id, reason, data=None) -> Optional[dict]:
        """Persist a terminal, content-free outcome when runtime rejects before workload creation."""
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            meeting = (
                await db.execute(
                    select(Meeting).where(Meeting.id == meeting_id).with_for_update()
                )
            ).scalars().first()
            if meeting is None:
                return None
            meeting.status = "failed"
            if meeting.end_time is None:
                meeting.end_time = datetime.now(timezone.utc).replace(tzinfo=None)
            merged = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            merged["failure_stage"] = "runtime_spawn"
            merged["spawn_failure_reason"] = reason
            patch = dict(data or {})
            if capture_is_withdrawn(merged):
                patch.pop("zaki_capture", None)
            merged.update(patch)
            meeting.data = merged
            flag_modified(meeting, "data")
            await db.commit()
            await db.refresh(meeting)
            return _row_to_dict(meeting)

    async def withdraw_capture(
        self, *, tenant_id, user_id, platform, native_meeting_id, withdrawn_at
    ) -> Optional[dict]:
        """Persist capture withdrawal under the exclusive meeting-write barrier.

        The per-user spawn lock is acquired before choosing the latest row, so capture creation and
        withdrawal have one serial order. The meeting-write lock then drains transcript/recording
        writers before the withdrawal mutation.
        """
        async with self._session_factory() as db:
            await db.execute(
                self._statement("SELECT pg_advisory_xact_lock(:uid)"),
                {"uid": user_id},
            )
            candidate = await db.execute(
                self._statement(
                    "SELECT id FROM meetings "
                    "WHERE user_id = :user_id AND platform = :platform "
                    "AND platform_specific_id = :native_meeting_id "
                    "AND data->'zaki_capture'->>'tenant_id' = :tenant_id "
                    "AND data->'zaki_capture'->>'state' IN ('authorized', 'withdrawn') "
                    "ORDER BY created_at DESC, id DESC LIMIT 1"
                ),
                {
                    "user_id": user_id,
                    "platform": platform,
                    "native_meeting_id": native_meeting_id,
                    "tenant_id": tenant_id,
                },
            )
            found = candidate.mappings().first()
            if found is None:
                return None
            meeting_id = int(found["id"])
            await db.execute(
                self._statement(
                    "SELECT pg_advisory_xact_lock(:lock_namespace, :meeting_id)"
                ),
                {
                    "lock_namespace": MEETING_WRITE_LOCK_NAMESPACE,
                    "meeting_id": meeting_id,
                },
            )
            selected = await db.execute(
                self._statement(
                    "SELECT id, user_id, platform, platform_specific_id, status, "
                    "bot_container_id, start_time, end_time, data, created_at, updated_at "
                    "FROM meetings WHERE id = :meeting_id FOR UPDATE"
                ),
                {"meeting_id": meeting_id},
            )
            row = selected.mappings().first()
            if row is None or int(row["user_id"]) != user_id:
                return None
            data = dict(row["data"]) if isinstance(row["data"], dict) else {}
            capture = data.get("zaki_capture")
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
                data["zaki_capture"] = capture
            data["stop_requested"] = True
            should_stop = prior_status not in ("completed", "failed")
            status = "stopping" if should_stop else prior_status
            await db.execute(
                self._statement(
                    "UPDATE meetings SET status = :status, data = CAST(:data AS jsonb) "
                    "WHERE id = :meeting_id"
                ),
                {
                    "status": status,
                    "data": json.dumps(data, sort_keys=True),
                    "meeting_id": meeting_id,
                },
            )
            await db.commit()
            meeting = {
                "id": meeting_id,
                "user_id": row["user_id"],
                "platform": row["platform"],
                "native_meeting_id": row["platform_specific_id"],
                "platform_specific_id": row["platform_specific_id"],
                "status": status,
                "bot_container_id": row["bot_container_id"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "data": data,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            return {
                "meeting": meeting,
                "changed": changed,
                "should_stop": should_stop,
                "prior_status": prior_status,
            }


class HttpRuntimeClient:
    """``RuntimeClient`` over the runtime.v1 HTTP kernel (``POST /workloads``). 429 → QuotaExceeded;
    non-201 → SpawnFailed (parent ``_spawn_via_runtime_api``)."""

    def __init__(self, client, runtime_api_url: str):
        self._client = client
        self._url = runtime_api_url.rstrip("/")

    async def create_workload(self, spec: dict) -> dict:
        resp = await self._client.post(f"{self._url}/workloads", json=spec, timeout=30.0)
        if resp.status_code == 429:
            raise QuotaExceeded("runtime kernel: owner quota exceeded")
        if resp.status_code != 201:
            raise SpawnFailed(f"runtime kernel returned {resp.status_code}")
        return resp.json()

    async def delete_workload(self, workload_id: str) -> None:
        """Tear down a workload (``DELETE /workloads/{id}``) — teardown must be CONFIRMED.

        A 2xx means the kernel destroyed the workload (with kernel re-adoption that reaches the
        real container even across a runtime restart). A 404 raises ``WorkloadUnknown``: the kernel
        does not know the workload, so termination is UNCONFIRMED — a container may still be live
        (the orphaned-live-bot incident treated exactly this 404 as success). Any other error
        raises ``SpawnFailed``. Callers log loud and retry/backstop; they must never report a stop
        as done on these."""
        resp = await self._client.delete(
            f"{self._url}/workloads/{workload_id}",
            timeout=60.0,  # the kernel's graceful teardown can hold the request for its stop grace
        )
        if resp.status_code == 404:
            raise WorkloadUnknown(workload_id)
        if resp.status_code >= 400:
            raise SpawnFailed(f"runtime kernel delete_workload returned {resp.status_code}")

    async def get_workload(self, workload_id: str) -> Optional[dict]:
        """Liveness probe (``GET /workloads/{id}``). 404 → the kernel does not TRACK the workload →
        ``None`` — which is NOT evidence the bot is gone (a recreated runtime forgets live bots);
        the reconcile sweep treats it as 'untracked: fail loud, do not reap'. Any other non-200
        raises (caller treats it as 'unknown, do not reap' — fail safe toward NOT killing a
        possibly-live meeting)."""
        resp = await self._client.get(f"{self._url}/workloads/{workload_id}", timeout=10.0)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise SpawnFailed(f"runtime kernel get_workload returned {resp.status_code}")
        return resp.json()


def build_production_router(*, database_url: Optional[str] = None, runtime_api_url: Optional[str] = None):
    """Construct the bot-spawn router with real SQLAlchemy + httpx runtime adapters from env."""
    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from .router import build_router

    database_url = database_url or os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/vexa"
    )
    runtime_api_url = runtime_api_url or os.getenv("RUNTIME_API_URL", "http://runtime:8090")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    http = httpx.AsyncClient(timeout=30.0)
    return build_router(SqlAlchemyMeetingRepo(session_factory), HttpRuntimeClient(http, runtime_api_url))
