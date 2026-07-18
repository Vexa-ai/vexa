"""Production-adapter tests for the body-free ZAKI read-index projection."""
from __future__ import annotations

from datetime import datetime

from meeting_api.collector.adapters import SqlAlchemyTranscriptStore


class _Result:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.sql = ""
        self.params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        self.sql = " ".join(str(statement).split())
        self.params = params
        return _Result(self._rows)


async def test_postgres_read_metadata_projection_is_owner_scoped_and_body_free():
    occurred_at = datetime(2026, 7, 16, 9, 0)
    session = _Session([
        {
            "id": 41,
            "user_id": 7,
            "platform": "google_meet",
            "status": "completed",
            "start_time": occurred_at,
            "end_time": datetime(2026, 7, 16, 10, 0),
            "title": "Launch readiness retrospective",
            "name": None,
            "zaki_capture": {"state": "authorized"},
            "zaki_read": {"enabled": True},
            "zaki_retention": {"state": "open"},
            "created_at": occurred_at,
            "updated_at": occurred_at,
            "meeting_available": True,
            "transcript_available": True,
            "summary_available": True,
            "summary_updated_at": "2026-07-16T10:06:00",
        }
    ])
    store = SqlAlchemyTranscriptStore(
        lambda: session,
        statement_factory=lambda sql: sql,
    )

    rows = await store.list_owned_read_metadata(7)

    assert session.params == {"user_id": 7}
    assert "WHERE m.user_id = :user_id" in session.sql
    assert "FROM transcriptions t" in session.sql
    assert "char_length(btrim(t.text)) BETWEEN 1 AND 65536" in session.sql
    assert "platform_specific_id" not in session.sql
    assert "m.data," not in session.sql
    assert rows == [{
        "id": 41,
        "user_id": 7,
        "platform": "google_meet",
        "status": "completed",
        "start_time": "2026-07-16T09:00:00+00:00",
        "end_time": "2026-07-16T10:00:00+00:00",
        "data": {
            "title": "Launch readiness retrospective",
            "zaki_capture": {"state": "authorized"},
            "zaki_read": {"enabled": True},
            "zaki_retention": {"state": "open"},
        },
        "created_at": "2026-07-16T09:00:00+00:00",
        "updated_at": "2026-07-16T09:00:00+00:00",
        "meeting_available": True,
        "transcript_available": True,
        "summary_available": True,
        "summary_updated_at": "2026-07-16T10:06:00+00:00",
    }]
    assert "private-native-id" not in str(rows)
    assert "segments" not in rows[0]
