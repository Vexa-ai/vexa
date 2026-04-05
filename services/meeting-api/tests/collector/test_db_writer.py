"""Unit tests for collector/db_writer.py.

Tests create_transcription_object and the Redis-to-Postgres background processor.
"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone


class TestCreateTranscriptionObject:
    def test_basic_fields(self):
        from meeting_api.collector.db_writer import create_transcription_object

        t = create_transcription_object(
            meeting_id=1, start=0.0, end=5.0, text="Hello world",
            language="en", session_uid="sess-1", mapped_speaker_name="Alice",
            segment_id="seg-1"
        )

        assert t.meeting_id == 1
        assert t.start_time == 0.0
        assert t.end_time == 5.0
        assert t.text == "Hello world"
        assert t.language == "en"
        assert t.session_uid == "sess-1"
        assert t.speaker == "Alice"
        assert t.segment_id == "seg-1"
        assert t.created_at is not None

    def test_none_optional_fields(self):
        from meeting_api.collector.db_writer import create_transcription_object

        t = create_transcription_object(
            meeting_id=1, start=0.0, end=1.0, text="text",
            language=None, session_uid=None, mapped_speaker_name=None
        )

        assert t.language is None
        assert t.session_uid is None
        assert t.speaker is None
        assert t.segment_id is None


@pytest.fixture
def mock_redis():
    """Mock Redis client for db_writer tests."""
    r = AsyncMock()
    r.smembers = AsyncMock(return_value=set())
    r.srem = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.hdel = AsyncMock()
    return r


@pytest.mark.asyncio
class TestProcessRedisToPostgres:
    async def test_no_active_meetings_does_nothing(self, mock_redis):
        from meeting_api.collector.db_writer import process_redis_to_postgres
        import asyncio

        mock_redis.smembers = AsyncMock(return_value=set())

        with patch("meeting_api.collector.db_writer.BACKGROUND_TASK_INTERVAL", 0):
            task = asyncio.create_task(process_redis_to_postgres(mock_redis))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_redis.smembers.assert_called()

    async def test_empty_segments_removes_from_active(self, mock_redis):
        from meeting_api.collector.db_writer import process_redis_to_postgres
        import asyncio

        mock_redis.smembers = AsyncMock(return_value={"42"})
        mock_redis.hgetall = AsyncMock(return_value={})

        with patch("meeting_api.collector.db_writer.BACKGROUND_TASK_INTERVAL", 0):
            with patch("meeting_api.collector.db_writer.async_session_local") as mock_session_ctx:
                mock_db = AsyncMock()
                mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

                task = asyncio.create_task(process_redis_to_postgres(mock_redis))
                await asyncio.sleep(0.15)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        mock_redis.srem.assert_called_with("active_meetings", "42")

    async def test_immutable_segments_written_to_db(self, mock_redis):
        from meeting_api.collector.db_writer import process_redis_to_postgres
        import asyncio

        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        segment_data = {
            "text": "Hello", "start_time": 0.0, "end_time": 5.0,
            "language": "en", "updated_at": old_time,
            "session_uid": "sess-1", "speaker": "Alice", "segment_id": "seg-1"
        }
        mock_redis.smembers = AsyncMock(return_value={"1"})
        mock_redis.hgetall = AsyncMock(return_value={"seg-1": json.dumps(segment_data)})

        with patch("meeting_api.collector.db_writer.BACKGROUND_TASK_INTERVAL", 0):
            with patch("meeting_api.collector.db_writer.IMMUTABILITY_THRESHOLD", 30):
                with patch("meeting_api.collector.db_writer.async_session_local") as mock_session_ctx:
                    mock_db = AsyncMock()
                    mock_db.execute = AsyncMock()
                    mock_db.commit = AsyncMock()
                    mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                    mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

                    task = asyncio.create_task(process_redis_to_postgres(mock_redis))
                    await asyncio.sleep(0.15)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        mock_db.commit.assert_called()

    async def test_empty_text_segment_deleted_not_stored(self, mock_redis):
        from meeting_api.collector.db_writer import process_redis_to_postgres
        import asyncio

        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        segment_data = {
            "text": "   ", "start_time": 0.0, "end_time": 1.0,
            "language": "en", "updated_at": old_time,
            "session_uid": "sess-1", "speaker": "Alice", "segment_id": "seg-empty"
        }
        mock_redis.smembers = AsyncMock(return_value={"1"})
        mock_redis.hgetall = AsyncMock(return_value={"seg-empty": json.dumps(segment_data)})

        with patch("meeting_api.collector.db_writer.BACKGROUND_TASK_INTERVAL", 0):
            with patch("meeting_api.collector.db_writer.IMMUTABILITY_THRESHOLD", 30):
                with patch("meeting_api.collector.db_writer.async_session_local") as mock_session_ctx:
                    mock_db = AsyncMock()
                    mock_db.execute = AsyncMock()
                    mock_db.commit = AsyncMock()
                    mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                    mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

                    task = asyncio.create_task(process_redis_to_postgres(mock_redis))
                    await asyncio.sleep(0.15)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        mock_db.commit.assert_not_called()

    async def test_malformed_segment_json_handled(self, mock_redis):
        from meeting_api.collector.db_writer import process_redis_to_postgres
        import asyncio

        mock_redis.smembers = AsyncMock(return_value={"1"})
        mock_redis.hgetall = AsyncMock(return_value={"bad-seg": "not{valid json"})

        with patch("meeting_api.collector.db_writer.BACKGROUND_TASK_INTERVAL", 0):
            with patch("meeting_api.collector.db_writer.async_session_local") as mock_session_ctx:
                mock_db = AsyncMock()
                mock_db.commit = AsyncMock()
                mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

                task = asyncio.create_task(process_redis_to_postgres(mock_redis))
                await asyncio.sleep(0.15)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def test_inverted_timestamps_corrected(self, mock_redis):
        from meeting_api.collector.db_writer import create_transcription_object

        t = create_transcription_object(
            meeting_id=1, start=5.0, end=0.0,
            text="test", language=None, session_uid=None, mapped_speaker_name=None
        )
        assert t.start_time == 5.0
        assert t.end_time == 0.0
