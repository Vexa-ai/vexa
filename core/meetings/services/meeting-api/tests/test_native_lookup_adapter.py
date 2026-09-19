"""The production lookup sends tenant/native constraints and returns every matching row."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
pytest.importorskip("sqlalchemy")
from sqlalchemy.dialects import postgresql

from meeting_api.collector.adapters import SqlAlchemyTranscriptStore
from meeting_api.collector.models import Meeting


@pytest.mark.asyncio
async def test_owned_native_lookup_is_scoped_and_unbounded():
    rows = [Meeting(id=i, user_id=7, platform='google_meet', platform_specific_id='room',
                    status='completed', data={}, created_at=datetime(2026, 1, i, tzinfo=timezone.utc))
            for i in [2, 1]]
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
    context = AsyncMock()
    context.__aenter__.return_value = db
    store = SqlAlchemyTranscriptStore(lambda: context)
    result = await store.find_owned_native_meetings(7, 'google_meet', 'room')
    query = db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert set(query.params.values()) == {7, 'google_meet', 'room'}
    sql = str(query)
    assert 'meetings.user_id =' in sql
    assert 'meetings.platform =' in sql
    assert 'meetings.platform_specific_id =' in sql
    assert 'ORDER BY meetings.created_at DESC, meetings.id DESC' in sql
    assert 'LIMIT' not in sql
    assert [row['id'] for row in result] == [2, 1]
    assert all(row['shared'] is False for row in result)
    assert result[0]['created_at']
