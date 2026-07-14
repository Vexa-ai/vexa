"""Production PostgreSQL/object-storage composition for bounded Minutes TTL batches."""
from __future__ import annotations

from datetime import datetime, timedelta
import json

from ..recordings.ports import MEETING_WRITE_LOCK_NAMESPACE
from .adapters import recording_prefixes_for_meeting
from .ttl import DueScope


class SqlAlchemyTtlStore:
    """Select and expire already-materialized per-scope retention deadlines."""

    def __init__(self, session_factory, object_storage, *, statement_factory=None):
        self._session_factory = session_factory
        self._object_storage = object_storage
        self._statement_factory = statement_factory

    def _statement(self, sql: str):
        if self._statement_factory is not None:
            return self._statement_factory(sql)
        from sqlalchemy import text

        return text(sql)

    @staticmethod
    def _meeting_id(value: str) -> int | None:
        try:
            meeting_id = int(value)
        except (TypeError, ValueError):
            return None
        return meeting_id if meeting_id > 0 else None

    @staticmethod
    def _stored_expiry(data: dict, scope: str) -> datetime | None:
        retention = data.get("zaki_retention")
        expiries = retention.get("scope_expiries") if isinstance(retention, dict) else None
        value = expiries.get(scope) if isinstance(expiries, dict) else None
        if not isinstance(value, str):
            return None
        try:
            expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if expiry.tzinfo is None or expiry.utcoffset() != timedelta(0):
            return None
        return expiry

    async def _exclusive_lock(self, db, meeting_id: int) -> None:
        await db.execute(
            self._statement(
                "SELECT pg_advisory_xact_lock(:lock_namespace, :meeting_id)"
            ),
            {
                "lock_namespace": MEETING_WRITE_LOCK_NAMESPACE,
                "meeting_id": meeting_id,
            },
        )

    async def _candidate(self, db, item: DueScope, meeting_id: int) -> dict | None:
        result = await db.execute(
            self._statement(
                "SELECT id, user_id, status, data FROM meetings "
                "WHERE id = :meeting_id FOR UPDATE"
            ),
            {"meeting_id": meeting_id},
        )
        row = result.mappings().first()
        if (
            row is None
            or str(row["user_id"]) != item.user_id
            or row["status"] not in {"completed", "failed"}
        ):
            return None
        data = dict(row["data"]) if isinstance(row["data"], dict) else {}
        retention = data.get("zaki_retention")
        if not isinstance(retention, dict) or retention.get("state") == "erasing":
            return None
        expired = retention.get("expired_scopes", [])
        if not isinstance(expired, list) or item.scope in expired:
            return None
        if self._stored_expiry(data, item.scope) != item.expires_at:
            return None
        return {"owner_id": row["user_id"], "data": data}

    async def _persist_data(self, db, meeting_id: int, owner_id, data: dict) -> None:
        result = await db.execute(
            self._statement(
                "UPDATE meetings SET data = CAST(:data AS jsonb) "
                "WHERE id = :meeting_id AND user_id = :user_id"
            ),
            {
                "meeting_id": meeting_id,
                "user_id": owner_id,
                "data": json.dumps(data, sort_keys=True),
            },
        )
        if int(result.rowcount or 0) != 1:
            raise RuntimeError("TTL meeting update lost its owner")

    @staticmethod
    def _mark_expired(data: dict, scope: str) -> None:
        retention = dict(data["zaki_retention"])
        expired = set(retention.get("expired_scopes", []))
        expired.add(scope)
        retention["expired_scopes"] = sorted(expired)
        data["zaki_retention"] = retention

    async def list_due_scopes(self, *, now, limit: int) -> tuple[DueScope, ...]:
        sql = """
            SELECT m.user_id, m.id AS meeting_id, due.scope,
                   CAST(due.expires_text AS timestamptz) AS expires_at
            FROM meetings m
            CROSS JOIN LATERAL (
                VALUES
                    ('audio', m.data #>> '{zaki_retention,scope_expiries,audio}'),
                    ('transcript', m.data #>> '{zaki_retention,scope_expiries,transcript}'),
                    ('summary', m.data #>> '{zaki_retention,scope_expiries,summary}')
            ) AS due(scope, expires_text)
            WHERE m.status IN ('completed', 'failed')
              AND COALESCE(m.data #>> '{zaki_retention,state}', 'open') <> 'erasing'
              AND due.expires_text IS NOT NULL
              AND CAST(due.expires_text AS timestamptz) <= :now
              AND NOT (
                  COALESCE(
                      m.data #> '{zaki_retention,expired_scopes}', '[]'::jsonb
                  ) ? due.scope
              )
            ORDER BY CAST(due.expires_text AS timestamptz), m.id, due.scope
            LIMIT :limit
        """
        async with self._session_factory() as db:
            result = await db.execute(
                self._statement(sql), {"now": now, "limit": limit}
            )
            return tuple(
                DueScope(
                    user_id=str(row["user_id"]),
                    meeting_id=str(row["meeting_id"]),
                    scope=row["scope"],
                    expires_at=row["expires_at"],
                )
                for row in result.mappings().all()
            )

    async def expire_scope(self, item: DueScope) -> int:
        """Expire one still-owned, unchanged terminal-meeting scope idempotently."""

        meeting_id = self._meeting_id(item.meeting_id)
        if meeting_id is None:
            return 0
        async with self._session_factory() as db:
            await self._exclusive_lock(db, meeting_id)
            candidate = await self._candidate(db, item, meeting_id)
            if candidate is None:
                return 0
            data = candidate["data"]
            if item.scope == "transcript":
                result = await db.execute(
                    self._statement(
                        "DELETE FROM transcriptions WHERE meeting_id = :meeting_id"
                    ),
                    {"meeting_id": meeting_id},
                )
                deleted = int(result.rowcount or 0)
            elif item.scope == "summary":
                summaries = data.get("summaries")
                deleted = len(summaries) if isinstance(summaries, list) else 0
                if data.get("summary") is not None:
                    deleted += 1
                data.pop("summary", None)
                data.pop("summaries", None)
            elif item.scope == "audio":
                if self._object_storage is None:
                    raise RuntimeError("audio TTL object storage is unavailable")
                prefixes = recording_prefixes_for_meeting(candidate["owner_id"], data)
                deleted = 0
                for prefix in prefixes:
                    deleted += await self._object_storage.delete_prefix(prefix)
                if any(
                    [
                        await self._object_storage.count_prefix(prefix)
                        for prefix in prefixes
                    ]
                ):
                    raise RuntimeError("audio TTL object deletion was incomplete")
                data["recordings"] = []
            else:
                raise RuntimeError("TTL scope is invalid")
            self._mark_expired(data, item.scope)
            await self._persist_data(db, meeting_id, candidate["owner_id"], data)
            await db.commit()
            return deleted
