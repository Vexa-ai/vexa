"""Production-style capture withdrawal adapter tests."""
from __future__ import annotations

import json

import pytest

from meeting_api.bot_spawn.adapters import SqlAlchemyMeetingRepo
from meeting_api.collector.adapters import SqlAlchemyTranscriptStore
from meeting_api.collector.ports import TranscriptWriteRefused


class _Result:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


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
        if sql.startswith("SELECT id FROM meetings"):
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


async def test_postgres_withdrawal_takes_exclusive_barrier_before_state_mutation():
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

    assert session.events == ["lookup", "lock", "row", "update", "commit"]
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
