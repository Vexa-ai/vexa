"""Production-style capture withdrawal adapter tests."""
from __future__ import annotations

import json
import sys
from types import MappingProxyType, SimpleNamespace

import pytest

from meeting_api.bot_spawn.adapters import SqlAlchemyMeetingRepo
from meeting_api.bot_spawn.ports import CaptureGrantConsumed
from meeting_api.collector.adapters import SqlAlchemyTranscriptStore
from meeting_api.collector.ports import TranscriptWriteRefused


class _Result:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row

    def scalar(self):
        return self._row

    def scalars(self):
        return self


class _Session:
    def __init__(self, meeting):
        self.meeting = meeting
        self.events: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        self.events.append("commit")

    async def execute(self, statement, params=None):
        sql = " ".join(statement.split())
        params = params or {}
        if "pg_advisory_xact_lock(:uid)" in sql:
            self.events.append("user_lock")
            return _Result()
        if sql.startswith("SELECT id FROM meetings"):
            assert "data->'zaki_capture'->>'tenant_id' = :tenant_id" in sql
            assert params["tenant_id"] == "tenant-a"
            self.events.append("lookup")
            return _Result({"id": self.meeting["id"]})
        if "pg_advisory_xact_lock" in sql:
            self.events.append("lock")
            return _Result()
        if sql.startswith("SELECT id, user_id, platform"):
            self.events.append("row")
            return _Result(dict(self.meeting))
        if sql.startswith("UPDATE meetings SET status"):
            self.events.append("update")
            self.meeting["status"] = params["status"]
            self.meeting["data"] = json.loads(params["data"])
            return _Result()
        raise AssertionError(f"unexpected SQL: {sql}")


async def test_postgres_withdrawal_serializes_with_spawn_before_selecting_the_capture():
    meeting = {
        "id": 41,
        "user_id": 7,
        "platform": "google_meet",
        "platform_specific_id": "abc-defg-hij",
        "status": "active",
        "bot_container_id": "mtg-41",
        "start_time": None,
        "end_time": None,
        "data": {
            "zaki_capture": {
                "tenant_id": "tenant-a",
                "state": "authorized",
            }
        },
        "created_at": None,
        "updated_at": None,
    }
    session = _Session(meeting)
    repo = SqlAlchemyMeetingRepo(
        lambda: session,
        statement_factory=lambda sql: sql,
    )

    result = await repo.withdraw_capture(
        tenant_id="tenant-a",
        user_id=7,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        withdrawn_at="2026-07-15T08:41:00+00:00",
    )

    assert session.events == ["user_lock", "lookup", "lock", "row", "update", "commit"]
    assert result["changed"] is True
    assert result["should_stop"] is True
    assert meeting["status"] == "stopping"
    assert meeting["data"]["zaki_capture"] == {
        "tenant_id": "tenant-a",
        "state": "withdrawn",
        "withdrawal_reason": "consent_withdrawn",
        "withdrawn_at": "2026-07-15T08:41:00+00:00",
    }
    assert meeting["data"]["stop_requested"] is True


class _GuardedSpawnSession:
    def __init__(self):
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        self.calls += 1
        if self.calls == 1:
            return _Result()
        if self.calls == 2:
            sql = " ".join(statement.split())
            assert "data->'zaki_capture'->>'tenant_id' = :tenant_id" in sql
            assert params["tenant_id"] == "tenant-a"
            return _Result(
                MappingProxyType({
                    "zaki_capture": {
                        "state": "withdrawn",
                        "withdrawn_at": "2026-07-15T08:32:00+00:00",
                    }
                })
            )
        raise AssertionError(f"unexpected guarded-spawn query: {statement}")


async def test_postgres_spawn_rejects_authority_that_predates_scope_withdrawal():
    repo = SqlAlchemyMeetingRepo(
        lambda: _GuardedSpawnSession(),
        statement_factory=lambda sql: sql,
    )

    with pytest.raises(CaptureGrantConsumed, match="predates"):
        await repo.create_meeting_guarded(
            user_id=7,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            data={
                "zaki_capture": {
                    "tenant_id": "tenant-a",
                    "state": "authorized",
                    "authorized_at": "2026-07-15T08:31:00+00:00",
                    "grant_id_sha256": "a" * 64,
                }
            },
        )


class _TranscriptSession:
    def __init__(self, data):
        self.data = data
        self.events: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        sql = " ".join(statement.split())
        if "pg_advisory_lock_shared" in sql:
            self.events.append("lock")
            return _Result()
        if sql.startswith("SELECT data FROM meetings"):
            self.events.append("state")
            return _Result({"data": self.data})
        if "pg_advisory_unlock_shared" in sql:
            self.events.append("unlock")
            return _Result()
        raise AssertionError(f"unexpected SQL: {sql}")


class _NoRedisWrite:
    def pipeline(self, **kwargs):
        raise AssertionError("withdrawn transcript reached Redis")


async def test_postgres_transcript_writer_refuses_after_withdrawal_under_shared_barrier():
    session = _TranscriptSession({"zaki_capture": {"state": "withdrawn"}})
    store = SqlAlchemyTranscriptStore(
        lambda: session,
        _NoRedisWrite(),
        statement_factory=lambda sql: sql,
    )

    with pytest.raises(TranscriptWriteRefused, match="not writable"):
        await store.append_segment(41, {"segment_id": "late"})

    assert session.events == ["lock", "state", "unlock"]


class _DurableTranscriptSession:
    def __init__(self, data):
        self.data = data
        self.events: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        self.events.append("commit")

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if "pg_advisory_xact_lock_shared" in sql:
            self.events.append("lock")
            return _Result()
        if sql.startswith("SELECT data FROM meetings"):
            self.events.append("state")
            return _Result({"data": self.data})
        if sql.startswith("INSERT INTO transcriptions"):
            self.events.append("write")
            return _Result()
        raise AssertionError(f"unexpected SQL: {sql}")


async def test_postgres_durable_transcript_flush_refuses_after_withdrawal(monkeypatch):
    monkeypatch.setitem(sys.modules, "sqlalchemy", SimpleNamespace(text=lambda sql: sql))
    session = _DurableTranscriptSession({"zaki_capture": {"state": "withdrawn"}})
    store = SqlAlchemyTranscriptStore(
        lambda: session,
        None,
        statement_factory=lambda sql: sql,
    )

    with pytest.raises(TranscriptWriteRefused, match="not writable"):
        await store.upsert_segments(
            41,
            [{"segment_id": "late", "start": 0, "end": 1, "text": "sensitive"}],
        )

    assert session.events == ["lock", "state"]
