"""AIS-164: SEGMENT_PIPELINE regression tests.

DoD: segments injected into Redis appear in the REST transcript endpoint.

Written fail-first per AIS-164 TDD directive. Root cause: app.state.redis_client
is not injected by the existing `client` fixture, so collector endpoints receive
redis_c=None and silently return 0 segments even when Redis has data.
"""
import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import (
    make_meeting,
    make_user,
    MockResult,
    TEST_MEETING_ID,
    TEST_NATIVE_MEETING_ID,
    TEST_SESSION_UID,
    TEST_PLATFORM,
)

SEGMENT_ID = "seg-ais164-001"
SEGMENT_DATA = {
    "text": "Hola mundo",
    "start_time": 1.0,
    "end_time": 2.5,
    "language": "es",
    "completed": True,
    "updated_at": "2026-06-05T00:00:00+00:00",
    "session_uid": TEST_SESSION_UID,
    "speaker": "Alice",
    "speaker_mapping_status": "PRODUCER_LABELED",
    "segment_id": SEGMENT_ID,
}


def _make_redis_with_segment():
    r = AsyncMock()
    r.hgetall = AsyncMock(return_value={SEGMENT_ID: json.dumps(SEGMENT_DATA)})
    r.hgetall_empty = AsyncMock(return_value={})
    return r


def _setup_db_for_transcript(mock_db, sessions=None, db_segments=None):
    """Mock DB query sequence for GET /transcripts/{platform}/{id}.

    Endpoint query order:
      1. select Meeting (find by user+platform+native_id)
      2. select MeetingSession (for absolute time computation)
      3. select Transcription (persisted segments)
    """
    meeting = make_meeting()
    _sessions = sessions or []
    _db_segments = db_segments or []
    call_count = 0

    async def multi_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MockResult([meeting])
        elif call_count == 2:
            return MockResult(_sessions)
        elif call_count == 3:
            return MockResult(_db_segments)
        return MockResult()

    mock_db.execute = AsyncMock(side_effect=multi_execute)


@pytest_asyncio.fixture
async def collector_client(mock_db):
    """AsyncClient wired for collector endpoint tests.

    Unlike the shared `client` fixture this one also injects redis_client
    into app.state — the fix for AIS-164.
    """
    from meeting_api.main import app
    from meeting_api.database import get_db
    from meeting_api.collector.auth import get_current_user as collector_auth

    test_user = make_user()
    mock_redis = _make_redis_with_segment()

    async def override_get_db():
        yield mock_db

    async def override_auth():
        return test_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[collector_auth] = override_auth
    app.state.redis_client = mock_redis  # AIS-164 fix

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac._mock_redis = mock_redis
        yield ac

    app.dependency_overrides.clear()
    if hasattr(app.state, "redis_client"):
        del app.state.redis_client


class TestSegmentPipelineRedisToEndpoint:
    """SEGMENT_PIPELINE DoD: Redis segment visible via GET /transcripts."""

    @pytest.mark.asyncio
    async def test_endpoint_returns_segments_after_redis_store(self, collector_client, mock_db):
        """AIS-164 primary case: 1 segment in Redis → endpoint returns it.

        Fails before fix: app.state.redis_client not set → redis_c=None → 0 segs.
        """
        mock_redis = collector_client._mock_redis
        mock_redis.hgetall = AsyncMock(return_value={SEGMENT_ID: json.dumps(SEGMENT_DATA)})
        _setup_db_for_transcript(mock_db)

        resp = await collector_client.get(f"/transcripts/google_meet/{TEST_NATIVE_MEETING_ID}")

        assert resp.status_code == 200, resp.text
        segments = resp.json()["segments"]
        assert len(segments) == 1, (
            f"Expected 1 segment from Redis, got {len(segments)}. "
            "app.state.redis_client not injected?"
        )
        assert segments[0]["text"] == "Hola mundo"

    @pytest.mark.asyncio
    async def test_endpoint_returns_postgres_segments_after_redis_cleared(self, collector_client, mock_db):
        """AIS-164 db_writer race: segment persisted to PG then cleared from Redis.

        Once db_writer moves a segment Redis→PG, endpoint must still return it.
        """
        from meeting_api.models import Transcription

        # Redis is empty — db_writer already flushed it
        collector_client._mock_redis.hgetall = AsyncMock(return_value={})

        pg_seg = MagicMock(spec=Transcription)
        pg_seg.meeting_id = TEST_MEETING_ID
        pg_seg.segment_id = SEGMENT_ID
        pg_seg.start_time = 1.0
        pg_seg.end_time = 2.5
        pg_seg.text = "Hola mundo"
        pg_seg.language = "es"
        pg_seg.speaker = "Alice"
        pg_seg.session_uid = TEST_SESSION_UID
        pg_seg.created_at = datetime.now(timezone.utc)

        _setup_db_for_transcript(mock_db, db_segments=[pg_seg])

        resp = await collector_client.get(f"/transcripts/google_meet/{TEST_NATIVE_MEETING_ID}")

        assert resp.status_code == 200, resp.text
        segments = resp.json()["segments"]
        assert len(segments) == 1
        assert segments[0]["text"] == "Hola mundo"

    @pytest.mark.asyncio
    async def test_endpoint_empty_text_segment_not_returned(self, collector_client, mock_db):
        """Legacy processor empty-text guard: segments with text='' must not appear."""
        empty_seg = dict(SEGMENT_DATA, text="", segment_id="seg-empty")
        collector_client._mock_redis.hgetall = AsyncMock(
            return_value={"seg-empty": json.dumps(empty_seg)}
        )
        _setup_db_for_transcript(mock_db)

        resp = await collector_client.get(f"/transcripts/google_meet/{TEST_NATIVE_MEETING_ID}")

        assert resp.status_code == 200
        assert len(resp.json()["segments"]) == 0
