"""Production-boundary proof for bounded Minutes TTL composition."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from meeting_api.retention import run_production_ttl_once
from meeting_api.retention.ttl import DueScope
from meeting_api.retention.ttl_adapters import SqlAlchemyTtlStore
from meeting_api.retention.fakes import InMemoryRetentionStorage


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


class FakeResult:
    def __init__(self, rows=()):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class SelectionSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        self.calls.append((" ".join(statement.split()), params or {}))
        return FakeResult(self.rows)


async def test_postgres_ttl_store_lists_a_bounded_deterministic_due_scope_batch():
    rows = [
        {
            "user_id": 7,
            "meeting_id": 41,
            "scope": "audio",
            "expires_at": NOW - timedelta(seconds=2),
        },
        {
            "user_id": 7,
            "meeting_id": 41,
            "scope": "transcript",
            "expires_at": NOW - timedelta(seconds=1),
        },
    ]
    session = SelectionSession(rows)
    store = SqlAlchemyTtlStore(
        lambda: session,
        object_storage=None,
        statement_factory=lambda sql: sql,
    )

    due = await store.list_due_scopes(now=NOW, limit=2)

    assert [(item.meeting_id, item.scope) for item in due] == [
        ("41", "audio"),
        ("41", "transcript"),
    ]
    sql, params = session.calls[0]
    assert "status IN ('completed', 'failed')" in sql
    assert "expired_scopes" in sql
    assert "ORDER BY" in sql
    assert "LIMIT :limit" in sql
    assert params == {"now": NOW, "limit": 2}


async def test_production_ttl_entry_point_is_no_io_when_operator_flag_is_off():
    def forbidden_session_factory():
        raise AssertionError("disabled TTL worker touched PostgreSQL")

    receipt = await run_production_ttl_once(
        enabled=False,
        now=NOW,
        limit=100,
        session_factory=forbidden_session_factory,
        object_storage=None,
    )

    assert receipt.attempted == 0
    assert receipt.expired == {"audio": 0, "transcript": 0, "summary": 0}
    assert receipt.failed == 0


@pytest.mark.parametrize(
    ("enabled", "limit", "message"),
    [
        ("true", 100, "operator flag"),
        (True, 0, "batch limit"),
        (True, 501, "batch limit"),
    ],
)
async def test_production_ttl_entry_point_rejects_malformed_activation_without_io(
    enabled, limit, message
):
    def forbidden_session_factory():
        raise AssertionError("invalid TTL configuration touched PostgreSQL")

    with pytest.raises(ValueError, match=message):
        await run_production_ttl_once(
            enabled=enabled,
            now=NOW,
            limit=limit,
            session_factory=forbidden_session_factory,
            object_storage=None,
        )


class MutationResult(FakeResult):
    def __init__(self, *, row=None, rowcount=0):
        super().__init__()
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row


class MutationSession:
    def __init__(self, meeting, *, transcript_rows=0):
        self.meeting = meeting
        self.transcript_rows = transcript_rows
        self.calls = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        self.commits += 1

    async def execute(self, statement, params=None):
        import json

        params = params or {}
        sql = " ".join(statement.split())
        self.calls.append((sql, params))
        if "pg_advisory_xact_lock" in sql:
            return MutationResult()
        if sql.startswith("SELECT id, user_id, status, data FROM meetings"):
            return MutationResult(row=dict(self.meeting) if self.meeting else None)
        if sql.startswith("DELETE FROM transcriptions"):
            deleted = self.transcript_rows
            self.transcript_rows = 0
            return MutationResult(rowcount=deleted)
        if sql.startswith("UPDATE meetings SET data"):
            self.meeting["data"] = json.loads(params["data"])
            return MutationResult(rowcount=1)
        raise AssertionError(f"unexpected SQL: {sql}")


async def test_ttl_store_expires_transcript_and_summary_idempotently_after_owner_recheck():
    meeting = {
        "id": 41,
        "user_id": 7,
        "status": "completed",
        "data": {
            "summary": {"text": "private"},
            "summaries": [{"text": "also private"}],
            "zaki_retention": {
                "scope_expiries": {
                    "transcript": NOW.isoformat(),
                    "summary": NOW.isoformat(),
                }
            },
        },
    }
    session = MutationSession(meeting, transcript_rows=3)
    store = SqlAlchemyTtlStore(
        lambda: session,
        object_storage=None,
        statement_factory=lambda sql: sql,
    )

    transcript = DueScope("7", "41", "transcript", NOW)
    summary = DueScope("7", "41", "summary", NOW)
    assert await store.expire_scope(transcript) == 3
    assert await store.expire_scope(summary) == 2
    assert await store.expire_scope(summary) == 0

    assert "summary" not in meeting["data"]
    assert "summaries" not in meeting["data"]
    assert meeting["data"]["zaki_retention"]["expired_scopes"] == [
        "summary",
        "transcript",
    ]
    assert session.commits == 2
    lock_calls = [sql for sql, _ in session.calls if "pg_advisory_xact_lock" in sql]
    assert len(lock_calls) == 3


async def test_ttl_store_expires_only_owned_audio_under_the_meeting_write_barrier():
    prefix = "recordings/7/91/session-a/"
    foreign_prefix = "recordings/8/92/session-b/"
    meeting = {
        "id": 41,
        "user_id": 7,
        "status": "failed",
        "data": {
            "zaki_recording_prefixes": [prefix],
            "recordings": [
                {
                    "id": 91,
                    "session_uid": "session-a",
                    "media_files": [
                        {"storage_path": f"{prefix}audio/master.wav"},
                    ],
                }
            ],
            "zaki_retention": {
                "scope_expiries": {"audio": NOW.isoformat()},
            },
        },
    }
    session = MutationSession(meeting)
    storage = InMemoryRetentionStorage()
    storage.seed(f"{prefix}audio/master.wav", b"private")
    storage.seed(f"{prefix}video/master.webm", b"private")
    storage.seed(f"{foreign_prefix}audio/master.wav", b"foreign")
    store = SqlAlchemyTtlStore(
        lambda: session,
        storage,
        statement_factory=lambda sql: sql,
    )
    audio = DueScope("7", "41", "audio", NOW)

    assert await store.expire_scope(audio) == 2
    assert await store.expire_scope(audio) == 0

    assert storage.snapshot(prefix) == {}
    assert storage.snapshot(foreign_prefix) == {
        f"{foreign_prefix}audio/master.wav": b"foreign"
    }
    assert meeting["data"]["recordings"] == []
    assert meeting["data"]["zaki_retention"]["expired_scopes"] == ["audio"]
    lock_calls = [sql for sql, _ in session.calls if "pg_advisory_xact_lock" in sql]
    assert len(lock_calls) == 2


async def test_ttl_store_does_not_mutate_a_meeting_owned_by_another_user():
    prefix = "recordings/7/91/session-a/"
    meeting = {
        "id": 41,
        "user_id": 7,
        "status": "completed",
        "data": {
            "zaki_recording_prefixes": [prefix],
            "recordings": [],
            "zaki_retention": {
                "scope_expiries": {"audio": NOW.isoformat()},
            },
        },
    }
    session = MutationSession(meeting)
    storage = InMemoryRetentionStorage()
    storage.seed(f"{prefix}audio/master.wav", b"private")
    store = SqlAlchemyTtlStore(
        lambda: session,
        storage,
        statement_factory=lambda sql: sql,
    )

    assert await store.expire_scope(DueScope("8", "41", "audio", NOW)) == 0

    assert storage.snapshot(prefix) == {
        f"{prefix}audio/master.wav": b"private"
    }
    assert "expired_scopes" not in meeting["data"]["zaki_retention"]
    assert session.commits == 0


class FailingStorage(InMemoryRetentionStorage):
    async def delete_prefix(self, prefix: str) -> int:
        raise RuntimeError("storage unavailable")


async def test_ttl_store_keeps_audio_due_when_object_deletion_fails():
    prefix = "recordings/7/91/session-a/"
    meeting = {
        "id": 41,
        "user_id": 7,
        "status": "completed",
        "data": {
            "zaki_recording_prefixes": [prefix],
            "recordings": [],
            "zaki_retention": {
                "scope_expiries": {"audio": NOW.isoformat()},
            },
        },
    }
    session = MutationSession(meeting)
    storage = FailingStorage()
    storage.seed(f"{prefix}audio/master.wav", b"private")
    store = SqlAlchemyTtlStore(
        lambda: session,
        storage,
        statement_factory=lambda sql: sql,
    )

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await store.expire_scope(DueScope("7", "41", "audio", NOW))

    assert storage.snapshot(prefix) == {
        f"{prefix}audio/master.wav": b"private"
    }
    assert "expired_scopes" not in meeting["data"]["zaki_retention"]
    assert session.commits == 0
