"""PostgreSQL-backed state and callback outbox for ``zaki-control.v1``.

Imports of SQLAlchemy remain lazy so the meeting-api's offline suite can exercise the
router with its in-memory port.  The schema is deliberately additive and local to the
control boundary: it does not alter the shared meetings tables.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from .ports import CallbackEvent, Capture, ErasureTarget, OperationClaim, Policy, Subject


_ALLOWED_STATES = {
    "requested", "joining", "awaiting_admission", "active", "stopping", "completed", "failed",
}


class SqlAlchemyControlStore:
    """Durable idempotency, policy, capture mapping, and callback outbox state."""

    def __init__(self, session_factory, *, statement_factory=None):
        self._session_factory = session_factory
        self._statement_factory = statement_factory

    def _statement(self, sql: str):
        if self._statement_factory is not None:
            return self._statement_factory(sql)
        from sqlalchemy import text

        return text(sql)

    async def ensure_schema(self) -> None:
        # `CREATE ... IF NOT EXISTS` is intentionally serial and additive.  The engine owns this
        # table family, unlike `meetings`, which remains owned by admin-api schema convergence.
        ddl = (
            """
            CREATE TABLE IF NOT EXISTS zaki_control_policies (
                tenant_id VARCHAR(160) NOT NULL,
                user_id BIGINT NOT NULL,
                capture_enabled BOOLEAN NOT NULL,
                agent_read_enabled BOOLEAN NOT NULL,
                policy_version VARCHAR(160) NOT NULL,
                audio_days INTEGER NOT NULL,
                transcript_days INTEGER NOT NULL,
                summary_days INTEGER NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (tenant_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS zaki_control_operations (
                tenant_id VARCHAR(160) NOT NULL,
                user_id BIGINT NOT NULL,
                operation VARCHAR(32) NOT NULL,
                idempotency_key VARCHAR(160) NOT NULL,
                request_sha256 CHAR(64) NOT NULL,
                operation_id VARCHAR(160) NOT NULL,
                status VARCHAR(16) NOT NULL,
                response JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ,
                lease_expires_at TIMESTAMPTZ,
                fence BIGINT NOT NULL DEFAULT 1,
                progress JSONB,
                PRIMARY KEY (tenant_id, user_id, operation, idempotency_key)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS zaki_control_subject_state (
                tenant_id VARCHAR(160) NOT NULL,
                user_id BIGINT NOT NULL,
                state VARCHAR(16) NOT NULL,
                erasure_operation_id VARCHAR(160),
                erasure_fence BIGINT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (tenant_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS zaki_control_captures (
                capture_id VARCHAR(160) PRIMARY KEY,
                tenant_id VARCHAR(160) NOT NULL,
                user_id BIGINT NOT NULL,
                operation_id VARCHAR(160) NOT NULL,
                reservation_id VARCHAR(160) NOT NULL,
                platform VARCHAR(32) NOT NULL,
                native_meeting_id VARCHAR(255) NOT NULL,
                meeting_id BIGINT,
                state VARCHAR(32) NOT NULL,
                failure_code VARCHAR(64),
                max_capture_seconds INTEGER NOT NULL DEFAULT 0,
                captured_seconds_total INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_zaki_control_captures_subject_meeting
            ON zaki_control_captures (tenant_id, user_id, meeting_id)
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_zaki_control_captures_subject_operation
            ON zaki_control_captures (tenant_id, user_id, operation_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS zaki_control_callback_outbox (
                event_id VARCHAR(160) PRIMARY KEY,
                body JSONB NOT NULL,
                tenant_id VARCHAR(160),
                user_id BIGINT,
                capture_id VARCHAR(160),
                terminal BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                delivered_at TIMESTAMPTZ,
                attempts INTEGER NOT NULL DEFAULT 0
            )
            """,
        )
        async with self._session_factory() as db:
            for statement in ddl:
                await db.execute(self._statement(statement))
            await db.execute(self._statement(
                "ALTER TABLE zaki_control_operations "
                "ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ"
            ))
            for statement in (
                "ALTER TABLE zaki_control_operations ADD COLUMN IF NOT EXISTS fence BIGINT NOT NULL DEFAULT 1",
                "ALTER TABLE zaki_control_operations ADD COLUMN IF NOT EXISTS progress JSONB",
                "ALTER TABLE zaki_control_captures ADD COLUMN IF NOT EXISTS max_capture_seconds INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE zaki_control_captures ADD COLUMN IF NOT EXISTS captured_seconds_total INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE zaki_control_callback_outbox ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(160)",
                "ALTER TABLE zaki_control_callback_outbox ADD COLUMN IF NOT EXISTS user_id BIGINT",
                "ALTER TABLE zaki_control_callback_outbox ADD COLUMN IF NOT EXISTS capture_id VARCHAR(160)",
                "ALTER TABLE zaki_control_callback_outbox ADD COLUMN IF NOT EXISTS terminal BOOLEAN NOT NULL DEFAULT FALSE",
            ):
                await db.execute(self._statement(statement))
            await db.commit()

    async def claim_operation(
        self, *, subject, operation, idempotency_key, request_sha256, operation_id
    ) -> OperationClaim:
        async with self._session_factory() as db:
            # Retain completed receipts for a bounded period.  A pending receipt is held by a
            # short lease: an expired worker can be recovered, while the capture mapping and
            # grant identity make that retry side-effect-safe.
            await db.execute(
                self._statement(
                    "DELETE FROM zaki_control_operations "
                    "WHERE status = 'completed' AND expires_at IS NOT NULL AND expires_at < now()"
                )
            )
            inserted = await db.execute(
                self._statement(
                    """
                    INSERT INTO zaki_control_operations
                    (tenant_id, user_id, operation, idempotency_key, request_sha256, operation_id, status, lease_expires_at, fence)
                    VALUES (:tenant_id, :user_id, :operation, :idempotency_key, :request_sha256, :operation_id, 'pending', now() + interval '2 minutes', 1)
                    ON CONFLICT DO NOTHING
                    RETURNING operation_id, fence, progress
                    """
                ),
                {
                    "tenant_id": subject.tenant_id,
                    "user_id": int(subject.user_id),
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                    "request_sha256": request_sha256,
                    "operation_id": operation_id,
                },
            )
            row = inserted.mappings().first()
            if row is not None:
                await db.commit()
                return OperationClaim("new", str(row["operation_id"]), fence=int(row["fence"]))
            prior = (
                await db.execute(
                    self._statement(
                        """
                        SELECT request_sha256, operation_id, response, status, lease_expires_at, fence, progress
                        FROM zaki_control_operations
                        WHERE tenant_id = :tenant_id AND user_id = :user_id
                          AND operation = :operation AND idempotency_key = :idempotency_key
                        FOR UPDATE
                        """
                    ),
                    {
                        "tenant_id": subject.tenant_id,
                        "user_id": int(subject.user_id),
                        "operation": operation,
                        "idempotency_key": idempotency_key,
                    },
                )
            ).mappings().first()
            if prior is None:
                await db.commit()
                # A concurrent cleanup/insert race has no safe replay identity.  Do not perform
                # a side effect until the Hub retries after database recovery.
                return OperationClaim("pending", operation_id)
            if prior["request_sha256"] != request_sha256:
                await db.commit()
                return OperationClaim("conflict", str(prior["operation_id"]), fence=int(prior["fence"]))
            response = prior["response"]
            if isinstance(response, dict):
                await db.commit()
                return OperationClaim(
                    "replay", str(prior["operation_id"]), dict(response),
                    fence=int(prior["fence"]),
                    progress=dict(prior["progress"]) if isinstance(prior["progress"], dict) else None,
                )
            if prior["status"] == "pending":
                renewed = await db.execute(
                    self._statement(
                        """
                        UPDATE zaki_control_operations
                        SET lease_expires_at = now() + interval '2 minutes', fence = fence + 1
                        WHERE tenant_id = :tenant_id AND user_id = :user_id
                          AND operation = :operation AND idempotency_key = :idempotency_key
                          AND status = 'pending'
                          AND (lease_expires_at IS NULL OR lease_expires_at <= now())
                        RETURNING operation_id, fence, progress
                        """
                    ),
                    {
                        "tenant_id": subject.tenant_id,
                        "user_id": int(subject.user_id),
                        "operation": operation,
                        "idempotency_key": idempotency_key,
                    },
                )
                reclaimed = renewed.mappings().first()
                await db.commit()
                if reclaimed is not None:
                    return OperationClaim(
                        "retry", str(reclaimed["operation_id"]), fence=int(reclaimed["fence"]),
                        progress=dict(reclaimed["progress"]) if isinstance(reclaimed["progress"], dict) else None,
                    )
                return OperationClaim(
                    "pending", str(prior["operation_id"]), fence=int(prior["fence"]),
                    progress=dict(prior["progress"]) if isinstance(prior["progress"], dict) else None,
                )
            await db.commit()
            return OperationClaim(
                "pending", str(prior["operation_id"]), fence=int(prior["fence"]),
                progress=dict(prior["progress"]) if isinstance(prior["progress"], dict) else None,
            )

    async def lookup_operation(self, *, subject, operation, idempotency_key, request_sha256) -> OperationClaim | None:
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    self._statement(
                        """
                        SELECT request_sha256, operation_id, response, status, fence, progress
                        FROM zaki_control_operations
                        WHERE tenant_id = :tenant_id AND user_id = :user_id
                          AND operation = :operation AND idempotency_key = :idempotency_key
                        """
                    ),
                    {
                        "tenant_id": subject.tenant_id,
                        "user_id": int(subject.user_id),
                        "operation": operation,
                        "idempotency_key": idempotency_key,
                    },
                )
            ).mappings().first()
        if row is None:
            return None
        if row["request_sha256"] != request_sha256:
            return OperationClaim("conflict", str(row["operation_id"]), fence=int(row["fence"]))
        response = row["response"]
        progress = dict(row["progress"]) if isinstance(row["progress"], dict) else None
        if isinstance(response, dict):
            return OperationClaim("replay", str(row["operation_id"]), dict(response), fence=int(row["fence"]), progress=progress)
        return OperationClaim("pending", str(row["operation_id"]), fence=int(row["fence"]), progress=progress)

    async def complete_operation(self, *, subject, operation, idempotency_key, response, fence) -> None:
        async with self._session_factory() as db:
            result = await db.execute(
                self._statement(
                    """
                    UPDATE zaki_control_operations
                    SET status = 'completed', response = CAST(:response AS jsonb),
                        completed_at = now(), expires_at = now() + interval '7 days',
                        lease_expires_at = NULL
                    WHERE tenant_id = :tenant_id AND user_id = :user_id
                      AND operation = :operation AND idempotency_key = :idempotency_key
                      AND status = 'pending'
                      AND fence = :fence
                    """
                ),
                {
                    "tenant_id": subject.tenant_id,
                    "user_id": int(subject.user_id),
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                    "response": json.dumps(response, sort_keys=True, separators=(",", ":")),
                    "fence": fence,
                },
            )
            if not result.rowcount:
                raise RuntimeError("control operation could not be completed")
            await db.commit()

    async def save_operation_progress(self, *, subject, operation, idempotency_key, fence, progress) -> None:
        async with self._session_factory() as db:
            result = await db.execute(
                self._statement(
                    """
                    UPDATE zaki_control_operations
                    SET progress = CAST(:progress AS jsonb), lease_expires_at = now() + interval '2 minutes'
                    WHERE tenant_id = :tenant_id AND user_id = :user_id
                      AND operation = :operation AND idempotency_key = :idempotency_key
                      AND status = 'pending' AND fence = :fence
                    """
                ),
                {
                    "tenant_id": subject.tenant_id,
                    "user_id": int(subject.user_id),
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                    "fence": fence,
                    "progress": json.dumps(progress, sort_keys=True, separators=(",", ":")),
                },
            )
            if not result.rowcount:
                raise RuntimeError("stale control operation fence")
            await db.commit()

    async def assert_operation_fence(self, *, subject, operation, idempotency_key, fence) -> None:
        """Fence an external side effect against a reclaimed idempotency lease.

        A worker must call this immediately before it creates, stops, or irreversibly erases.
        A later executor cannot use a stale lease generation merely because its original request
        was slow: the conditional update both renews the live lease and proves ownership.
        """
        async with self._session_factory() as db:
            result = await db.execute(
                self._statement(
                    """
                    UPDATE zaki_control_operations
                    SET lease_expires_at = now() + interval '2 minutes'
                    WHERE tenant_id = :tenant_id AND user_id = :user_id
                      AND operation = :operation AND idempotency_key = :idempotency_key
                      AND status = 'pending' AND fence = :fence
                    """
                ),
                {
                    "tenant_id": subject.tenant_id,
                    "user_id": int(subject.user_id),
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                    "fence": fence,
                },
            )
            if not result.rowcount:
                raise RuntimeError("stale control operation fence")
            await db.commit()

    async def get_policy(self, subject: Subject) -> Policy | None:
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    self._statement(
                        """
                        SELECT capture_enabled, agent_read_enabled, policy_version,
                               audio_days, transcript_days, summary_days
                        FROM zaki_control_policies
                        WHERE tenant_id = :tenant_id AND user_id = :user_id
                        """
                    ),
                    {"tenant_id": subject.tenant_id, "user_id": int(subject.user_id)},
                )
            ).mappings().first()
        if row is None:
            return None
        return Policy(
            capture_enabled=bool(row["capture_enabled"]),
            agent_read_enabled=bool(row["agent_read_enabled"]),
            policy_version=str(row["policy_version"]),
            audio_days=int(row["audio_days"]),
            transcript_days=int(row["transcript_days"]),
            summary_days=int(row["summary_days"]),
        )

    async def _lock_subject(self, db, subject: Subject) -> None:
        """Serialize capture admission, reprovisioning, and account erasure for one subject.

        The control tables use a tenant/user compound identity while legacy ``meetings`` only has a
        numeric user id.  A two-key transaction advisory lock prevents a capture from slipping past
        the durable erasure barrier between separate table writes.
        """
        await db.execute(
            self._statement(
                "SELECT pg_advisory_xact_lock(hashtextextended(:tenant_id || ':' || :user_id::text, 0))"
            ),
            {"tenant_id": subject.tenant_id, "user_id": int(subject.user_id)},
        )

    async def put_policy(self, subject: Subject, policy: Policy) -> bool:
        async with self._session_factory() as db:
            await self._lock_subject(db, subject)
            state_row = (
                await db.execute(
                    self._statement(
                        "SELECT state FROM zaki_control_subject_state "
                        "WHERE tenant_id = :tenant_id AND user_id = :user_id FOR UPDATE"
                    ),
                    {"tenant_id": subject.tenant_id, "user_id": int(subject.user_id)},
                )
            ).mappings().first()
            if state_row is not None and state_row["state"] == "erasing":
                await db.commit()
                return False
            await db.execute(
                self._statement(
                    """
                    INSERT INTO zaki_control_policies
                    (tenant_id, user_id, capture_enabled, agent_read_enabled, policy_version,
                     audio_days, transcript_days, summary_days, updated_at)
                    VALUES (:tenant_id, :user_id, :capture_enabled, :agent_read_enabled, :policy_version,
                            :audio_days, :transcript_days, :summary_days, now())
                    ON CONFLICT (tenant_id, user_id) DO UPDATE SET
                      capture_enabled = EXCLUDED.capture_enabled,
                      agent_read_enabled = EXCLUDED.agent_read_enabled,
                      policy_version = EXCLUDED.policy_version,
                      audio_days = EXCLUDED.audio_days,
                      transcript_days = EXCLUDED.transcript_days,
                      summary_days = EXCLUDED.summary_days,
                      updated_at = now()
                    """
                ),
                {
                    "tenant_id": subject.tenant_id,
                    "user_id": int(subject.user_id),
                    "capture_enabled": policy.capture_enabled,
                    "agent_read_enabled": policy.agent_read_enabled,
                    "policy_version": policy.policy_version,
                    "audio_days": policy.audio_days,
                    "transcript_days": policy.transcript_days,
                    "summary_days": policy.summary_days,
                },
            )
            await db.execute(
                self._statement(
                    """
                    INSERT INTO zaki_control_subject_state
                    (tenant_id, user_id, state, erasure_operation_id, erasure_fence, updated_at)
                    VALUES (:tenant_id, :user_id, 'active', NULL, NULL, now())
                    ON CONFLICT (tenant_id, user_id) DO UPDATE SET
                      state = 'active', erasure_operation_id = NULL, erasure_fence = NULL, updated_at = now()
                    """
                ),
                {"tenant_id": subject.tenant_id, "user_id": int(subject.user_id)},
            )
            await db.commit()
        return True

    async def subject_is_erasing(self, subject: Subject) -> bool:
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    self._statement(
                        "SELECT state FROM zaki_control_subject_state "
                        "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                    ),
                    {"tenant_id": subject.tenant_id, "user_id": int(subject.user_id)},
                )
            ).mappings().first()
        return row is not None and row["state"] == "erasing"

    async def begin_subject_erasure(self, *, subject: Subject, operation_id: str, fence: int) -> bool:
        async with self._session_factory() as db:
            await self._lock_subject(db, subject)
            row = (
                await db.execute(
                    self._statement(
                        """
                        SELECT state, erasure_operation_id, erasure_fence
                        FROM zaki_control_subject_state
                        WHERE tenant_id = :tenant_id AND user_id = :user_id FOR UPDATE
                        """
                    ),
                    {"tenant_id": subject.tenant_id, "user_id": int(subject.user_id)},
                )
            ).mappings().first()
            if row is not None and row["state"] == "erasing":
                if row["erasure_operation_id"] != operation_id:
                    await db.commit()
                    return False
                # The same idempotency operation may be recovered after its old executor's
                # lease expired.  Transfer the barrier to the new fence; the old executor's
                # finish call then fails closed.
                if row["erasure_fence"] != fence:
                    await db.execute(
                        self._statement(
                            """
                            UPDATE zaki_control_subject_state
                            SET erasure_fence = :fence, updated_at = now()
                            WHERE tenant_id = :tenant_id AND user_id = :user_id
                              AND state = 'erasing' AND erasure_operation_id = :operation_id
                            """
                        ),
                        {
                            "tenant_id": subject.tenant_id,
                            "user_id": int(subject.user_id),
                            "operation_id": operation_id,
                            "fence": fence,
                        },
                    )
                await db.commit()
                return True
            await db.execute(
                self._statement(
                    """
                    INSERT INTO zaki_control_subject_state
                    (tenant_id, user_id, state, erasure_operation_id, erasure_fence, updated_at)
                    VALUES (:tenant_id, :user_id, 'erasing', :operation_id, :fence, now())
                    ON CONFLICT (tenant_id, user_id) DO UPDATE SET
                      state = 'erasing', erasure_operation_id = EXCLUDED.erasure_operation_id,
                      erasure_fence = EXCLUDED.erasure_fence, updated_at = now()
                    """
                ),
                {
                    "tenant_id": subject.tenant_id, "user_id": int(subject.user_id),
                    "operation_id": operation_id, "fence": fence,
                },
            )
            await db.commit()
        return True

    async def finish_subject_erasure(self, *, subject: Subject, operation_id: str, fence: int) -> None:
        async with self._session_factory() as db:
            await self._lock_subject(db, subject)
            result = await db.execute(
                self._statement(
                    """
                    UPDATE zaki_control_subject_state
                    SET state = 'erased', erasure_operation_id = NULL, erasure_fence = NULL, updated_at = now()
                    WHERE tenant_id = :tenant_id AND user_id = :user_id
                      AND state = 'erasing' AND erasure_operation_id = :operation_id
                      AND erasure_fence = :fence
                    """
                ),
                {
                    "tenant_id": subject.tenant_id, "user_id": int(subject.user_id),
                    "operation_id": operation_id, "fence": fence,
                },
            )
            if not result.rowcount:
                raise RuntimeError("stale subject erasure fence")
            await db.commit()

    async def create_capture(self, capture: Capture) -> None:
        async with self._session_factory() as db:
            await self._lock_subject(db, capture.subject)
            state = (
                await db.execute(
                    self._statement(
                        "SELECT state FROM zaki_control_subject_state "
                        "WHERE tenant_id = :tenant_id AND user_id = :user_id FOR UPDATE"
                    ),
                    {"tenant_id": capture.subject.tenant_id, "user_id": int(capture.subject.user_id)},
                )
            ).mappings().first()
            if state is not None and state["state"] == "erasing":
                await db.commit()
                raise RuntimeError("subject erasure is in progress")
            await db.execute(
                self._statement(
                    """
                    INSERT INTO zaki_control_captures
                    (capture_id, tenant_id, user_id, operation_id, reservation_id, platform,
                     native_meeting_id, meeting_id, state, failure_code, max_capture_seconds,
                     captured_seconds_total)
                    VALUES (:capture_id, :tenant_id, :user_id, :operation_id, :reservation_id, :platform,
                            :native_meeting_id, :meeting_id, :state, :failure_code, :max_capture_seconds,
                            :captured_seconds_total)
                    """
                ),
                {
                    "capture_id": capture.capture_id,
                    "tenant_id": capture.subject.tenant_id,
                    "user_id": int(capture.subject.user_id),
                    "operation_id": capture.operation_id,
                    "reservation_id": capture.reservation_id,
                    "platform": capture.platform,
                    "native_meeting_id": capture.native_meeting_id,
                    "meeting_id": int(capture.meeting_id) if capture.meeting_id is not None else None,
                    "state": capture.state,
                    "failure_code": capture.failure_code,
                    "max_capture_seconds": max(0, capture.max_capture_seconds),
                    "captured_seconds_total": max(0, capture.captured_seconds_total),
                },
            )
            await db.commit()

    async def bind_capture_meeting(self, *, capture_id: str, meeting_id: str) -> None:
        async with self._session_factory() as db:
            result = await db.execute(
                self._statement(
                    """
                    UPDATE zaki_control_captures
                    SET meeting_id = :meeting_id, state = 'requested', failure_code = NULL, updated_at = now()
                    WHERE capture_id = :capture_id
                    """
                ),
                {"capture_id": capture_id, "meeting_id": int(meeting_id)},
            )
            if not result.rowcount:
                raise RuntimeError("control capture could not be bound")
            await db.commit()

    @staticmethod
    def _capture_from_row(row: Any) -> Capture:
        state = str(row.get("meeting_state") or row["state"])
        failure_code = row["failure_code"]
        if state not in _ALLOWED_STATES:
            state = "failed"
            failure_code = "internal_failure"
        seconds = max(0, int(row.get("captured_seconds_total") or 0))
        started, ended = row.get("start_time"), row.get("end_time")
        if isinstance(started, datetime) and isinstance(ended, datetime):
            seconds = max(seconds, int((ended - started).total_seconds()))
        max_capture_seconds = max(0, int(row.get("max_capture_seconds") or 0))
        if max_capture_seconds:
            seconds = min(seconds, max_capture_seconds)
        started_at = started if isinstance(started, datetime) else row.get("created_at")
        if not isinstance(started_at, datetime):
            started_at = None
        return Capture(
            capture_id=str(row["capture_id"]),
            subject=Subject(str(row["tenant_id"]), str(row["user_id"])),
            operation_id=str(row["operation_id"]),
            reservation_id=str(row["reservation_id"]),
            platform=str(row["platform"]),
            native_meeting_id=str(row["native_meeting_id"]),
            meeting_id=str(row["meeting_id"]) if row["meeting_id"] is not None else None,
            state=state,
            failure_code=str(failure_code) if failure_code else None,
            captured_seconds_total=seconds,
            max_capture_seconds=max_capture_seconds,
            started_at=started_at,
        )

    async def get_capture(self, *, subject: Subject, capture_id: str) -> Capture | None:
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    self._statement(
                        """
                        SELECT c.*, m.status AS meeting_state, m.start_time, m.end_time
                        FROM zaki_control_captures c
                        LEFT JOIN meetings m ON m.id = c.meeting_id AND m.user_id = c.user_id
                        WHERE c.capture_id = :capture_id AND c.tenant_id = :tenant_id AND c.user_id = :user_id
                        """
                    ),
                    {
                        "capture_id": capture_id,
                        "tenant_id": subject.tenant_id,
                        "user_id": int(subject.user_id),
                    },
                )
            ).mappings().first()
        return self._capture_from_row(row) if row is not None else None

    async def get_capture_by_operation(
        self, *, subject: Subject, operation_id: str
    ) -> Capture | None:
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    self._statement(
                        """
                        SELECT c.*, m.status AS meeting_state, m.start_time, m.end_time
                        FROM zaki_control_captures c
                        LEFT JOIN meetings m ON m.id = c.meeting_id AND m.user_id = c.user_id
                        WHERE c.operation_id = :operation_id
                          AND c.tenant_id = :tenant_id AND c.user_id = :user_id
                        """
                    ),
                    {
                        "operation_id": operation_id,
                        "tenant_id": subject.tenant_id,
                        "user_id": int(subject.user_id),
                    },
                )
            ).mappings().first()
        return self._capture_from_row(row) if row is not None else None

    async def get_capture_for_meeting(self, meeting_id: str) -> Capture | None:
        try:
            numeric_meeting_id = int(meeting_id)
        except (TypeError, ValueError):
            return None
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    self._statement(
                        """
                        SELECT c.*, m.status AS meeting_state, m.start_time, m.end_time
                        FROM zaki_control_captures c
                        LEFT JOIN meetings m ON m.id = c.meeting_id AND m.user_id = c.user_id
                        WHERE c.meeting_id = :meeting_id
                        """
                    ),
                    {"meeting_id": numeric_meeting_id},
                )
            ).mappings().first()
        return self._capture_from_row(row) if row is not None else None

    async def capture_meetings_needing_reconciliation(self, *, limit: int) -> tuple[dict, ...]:
        """Return durable bot lifecycle rows which advanced before their control map was usable."""
        async with self._session_factory() as db:
            rows = (
                await db.execute(
                    self._statement(
                        """
                        SELECT m.id, m.status, m.data
                        FROM zaki_control_captures c
                        JOIN meetings m ON m.id = c.meeting_id AND m.user_id = c.user_id
                        WHERE m.status IS NOT NULL
                          AND m.status <> 'requested'
                          AND m.status IS DISTINCT FROM c.state
                        ORDER BY c.updated_at, c.capture_id
                        LIMIT :limit
                        """
                    ),
                    {"limit": max(1, min(int(limit), 500))},
                )
            ).mappings().all()
        return tuple({"id": row["id"], "status": row["status"], "data": row["data"]} for row in rows)

    async def mark_capture_state(self, *, capture_id: str, state: str, failure_code: str | None = None) -> None:
        async with self._session_factory() as db:
            await db.execute(
                self._statement(
                    """
                    UPDATE zaki_control_captures
                    SET state = :state, failure_code = :failure_code, updated_at = now()
                    WHERE capture_id = :capture_id
                    """
                ),
                {"capture_id": capture_id, "state": state, "failure_code": failure_code},
            )
            await db.commit()

    async def list_owned_erasure_targets(self, subject: Subject) -> tuple[ErasureTarget, ...]:
        async with self._session_factory() as db:
            rows = (
                await db.execute(
                    self._statement(
                        """
                        SELECT DISTINCT ON (m.id)
                          m.id AS meeting_id,
                          m.platform,
                          m.platform_specific_id AS native_meeting_id,
                          m.status AS meeting_state,
                          c.capture_id
                        FROM meetings m
                        LEFT JOIN zaki_control_captures c
                          ON c.meeting_id = m.id
                         AND c.tenant_id = :tenant_id
                         AND c.user_id = :user_id
                        WHERE m.user_id = :user_id
                          AND (
                            c.capture_id IS NOT NULL
                            OR COALESCE(m.data -> 'zaki_capture' ->> 'tenant_id', '') = :tenant_id
                          )
                        ORDER BY m.id, c.capture_id NULLS LAST
                        """
                    ),
                    {"tenant_id": subject.tenant_id, "user_id": int(subject.user_id)},
                )
            ).mappings().all()
        return tuple(
            ErasureTarget(
                meeting_id=str(row["meeting_id"]),
                subject=subject,
                platform=str(row["platform"] or ""),
                native_meeting_id=str(row["native_meeting_id"] or ""),
                state=str(row["meeting_state"] or "failed"),
                capture_id=str(row["capture_id"]) if row["capture_id"] is not None else None,
            )
            for row in rows
        )

    async def get_erasure_target(self, *, subject: Subject, meeting_id: str) -> ErasureTarget | None:
        try:
            numeric_meeting_id = int(meeting_id)
        except (TypeError, ValueError):
            return None
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    self._statement(
                        """
                        SELECT m.id AS meeting_id,
                               m.platform,
                               m.platform_specific_id AS native_meeting_id,
                               m.status AS meeting_state,
                               c.capture_id
                        FROM meetings m
                        LEFT JOIN zaki_control_captures c
                          ON c.meeting_id = m.id
                         AND c.tenant_id = :tenant_id
                         AND c.user_id = :user_id
                        WHERE m.id = :meeting_id AND m.user_id = :user_id
                          AND (
                            c.capture_id IS NOT NULL
                            OR COALESCE(m.data -> 'zaki_capture' ->> 'tenant_id', '') = :tenant_id
                          )
                        """
                    ),
                    {
                        "tenant_id": subject.tenant_id,
                        "user_id": int(subject.user_id),
                        "meeting_id": numeric_meeting_id,
                    },
                )
            ).mappings().first()
        if row is not None:
            return ErasureTarget(
                meeting_id=str(row["meeting_id"]),
                subject=subject,
                platform=str(row["platform"] or ""),
                native_meeting_id=str(row["native_meeting_id"] or ""),
                state=str(row["meeting_state"] or "failed"),
                capture_id=str(row["capture_id"]) if row["capture_id"] is not None else None,
            )
        # The raw meeting may have been deleted after its retention commit but before control
        # finalization. Keep the mapping discoverable so a retry can finish settlement cleanup.
        async with self._session_factory() as db:
            capture_row = (
                await db.execute(
                    self._statement(
                        """
                        SELECT capture_id, platform, native_meeting_id, state
                        FROM zaki_control_captures
                        WHERE tenant_id = :tenant_id AND user_id = :user_id
                          AND meeting_id = :meeting_id
                        """
                    ),
                    {
                        "tenant_id": subject.tenant_id,
                        "user_id": int(subject.user_id),
                        "meeting_id": numeric_meeting_id,
                    },
                )
            ).mappings().first()
        if capture_row is None:
            return None
        return ErasureTarget(
            meeting_id=str(meeting_id),
            subject=subject,
            platform=str(capture_row["platform"]),
            native_meeting_id=str(capture_row["native_meeting_id"]),
            state=str(capture_row["state"]),
            capture_id=str(capture_row["capture_id"]),
        )


    async def erase_subject_control_data(self, subject: Subject) -> None:
        # Operation rows are retained as minimal idempotency receipts for seven days; policy and
        # capture mapping data are removed only after every raw Minutes meeting erase succeeds.
        async with self._session_factory() as db:
            params = {"tenant_id": subject.tenant_id, "user_id": int(subject.user_id)}
            await db.execute(
                self._statement(
                    "DELETE FROM zaki_control_captures WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                params,
            )
            await db.execute(
                self._statement(
                    "DELETE FROM zaki_control_policies WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                params,
            )
            await db.execute(
                self._statement(
                    "DELETE FROM zaki_control_callback_outbox "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                params,
            )
            await db.commit()

    async def record_capture_transition(
        self, *, capture: Capture, state: str, failure_code: str | None, events: tuple[CallbackEvent, ...]
    ) -> None:
        """Commit a capture state advance and every deterministic callback in one transaction."""
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    self._statement(
                        "SELECT capture_id FROM zaki_control_captures WHERE capture_id = :capture_id FOR UPDATE"
                    ),
                    {"capture_id": capture.capture_id},
                )
            ).mappings().first()
            if row is None:
                await db.commit()
                return
            await db.execute(
                self._statement(
                    """
                    UPDATE zaki_control_captures
                    SET state = :state,
                        failure_code = :failure_code,
                        captured_seconds_total = GREATEST(captured_seconds_total, :captured_seconds_total),
                        updated_at = now()
                    WHERE capture_id = :capture_id
                    """
                ),
                {
                    "capture_id": capture.capture_id,
                    "state": state,
                    "failure_code": failure_code,
                    "captured_seconds_total": max(0, capture.captured_seconds_total),
                },
            )
            for event in events:
                await db.execute(
                    self._statement(
                        """
                        INSERT INTO zaki_control_callback_outbox
                        (event_id, body, tenant_id, user_id, capture_id, terminal)
                        VALUES (:event_id, CAST(:body AS jsonb), :tenant_id, :user_id, :capture_id, :terminal)
                        ON CONFLICT (event_id) DO NOTHING
                        """
                    ),
                    {
                        "event_id": event.event_id,
                        "body": json.dumps(event.body, sort_keys=True, separators=(",", ":")),
                        "tenant_id": event.subject.tenant_id if event.subject else None,
                        "user_id": int(event.subject.user_id) if event.subject else None,
                        "capture_id": event.capture_id,
                        "terminal": event.terminal,
                    },
                )
            await db.commit()


    async def pending_callbacks(self, *, limit: int, capture_id: str | None = None) -> tuple[CallbackEvent, ...]:
        async with self._session_factory() as db:
            rows = (
                await db.execute(
                    self._statement(
                        """
                        SELECT event_id, body, tenant_id, user_id, capture_id, terminal
                        FROM zaki_control_callback_outbox
                        WHERE delivered_at IS NULL
                          -- asyncpg prepares server-side: a bare NULL parameter in
                          -- ":x IS NULL OR col = :x" has no inferable type and raises
                          -- AmbiguousParameterError. The cast anchors it to the column type.
                          AND (CAST(:capture_id AS VARCHAR(160)) IS NULL
                               OR capture_id = CAST(:capture_id AS VARCHAR(160)))
                        ORDER BY created_at, event_id
                        LIMIT :limit
                        """
                    ),
                    {"limit": limit, "capture_id": capture_id},
                )
            ).mappings().all()
        return tuple(
            CallbackEvent(
                str(row["event_id"]), dict(row["body"]),
                Subject(str(row["tenant_id"]), str(row["user_id"]))
                if row["tenant_id"] is not None and row["user_id"] is not None else None,
                str(row["capture_id"]) if row["capture_id"] is not None else None,
                bool(row["terminal"]),
            )
            for row in rows
        )

    async def mark_callback_delivered(self, event_id: str) -> None:
        async with self._session_factory() as db:
            await db.execute(
                self._statement(
                    """
                    UPDATE zaki_control_callback_outbox
                    SET delivered_at = now(), attempts = attempts + 1
                    WHERE event_id = :event_id AND delivered_at IS NULL
                    """
                ),
                {"event_id": event_id},
            )
            await db.commit()

    async def mark_callback_failed(self, event_id: str) -> None:
        async with self._session_factory() as db:
            await db.execute(
                self._statement(
                    """
                    UPDATE zaki_control_callback_outbox SET attempts = attempts + 1
                    WHERE event_id = :event_id AND delivered_at IS NULL
                    """
                ),
                {"event_id": event_id},
            )
            await db.commit()

    async def terminal_callbacks_delivered(self, capture_id: str) -> bool:
        async with self._session_factory() as db:
            rows = (
                await db.execute(
                    self._statement(
                        """
                        SELECT count(*) AS total,
                               count(*) FILTER (WHERE delivered_at IS NOT NULL) AS delivered
                        FROM zaki_control_callback_outbox
                        WHERE capture_id = :capture_id AND terminal = TRUE
                        """
                    ),
                    {"capture_id": capture_id},
                )
            ).mappings().first()
        return bool(rows and int(rows["total"] or 0) > 0 and int(rows["total"]) == int(rows["delivered"]))

    async def finalize_erased_capture(self, *, subject: Subject, meeting_id: str) -> None:
        try:
            numeric_meeting_id = int(meeting_id)
        except (TypeError, ValueError):
            return
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    self._statement(
                        """
                        SELECT capture_id FROM zaki_control_captures
                        WHERE tenant_id = :tenant_id AND user_id = :user_id AND meeting_id = :meeting_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "tenant_id": subject.tenant_id,
                        "user_id": int(subject.user_id),
                        "meeting_id": numeric_meeting_id,
                    },
                )
            ).mappings().first()
            if row is None:
                await db.commit()
                return
            capture_id = str(row["capture_id"])
            pending = (
                await db.execute(
                    self._statement(
                        """
                        SELECT count(*) FROM zaki_control_callback_outbox
                        WHERE capture_id = :capture_id AND terminal = TRUE AND delivered_at IS NULL
                        """
                    ),
                    {"capture_id": capture_id},
                )
            ).scalar_one()
            terminal = (
                await db.execute(
                    self._statement(
                        "SELECT count(*) FROM zaki_control_callback_outbox "
                        "WHERE capture_id = :capture_id AND terminal = TRUE"
                    ),
                    {"capture_id": capture_id},
                )
            ).scalar_one()
            if int(terminal or 0) == 0 or int(pending or 0) != 0:
                raise RuntimeError("terminal settlement is not delivered")
            await db.execute(
                self._statement("DELETE FROM zaki_control_callback_outbox WHERE capture_id = :capture_id"),
                {"capture_id": capture_id},
            )
            await db.execute(
                self._statement("DELETE FROM zaki_control_captures WHERE capture_id = :capture_id"),
                {"capture_id": capture_id},
            )
            await db.commit()
