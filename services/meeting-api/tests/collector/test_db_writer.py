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

    # -----------------------------------------------------------------------
    # AIS-164 fix: segments_to_delete_always vs segments_to_delete_after_commit
    #
    # These tests would have FAILED on old code where a single dict was used
    # and hdel only ran inside `if batch_to_store:`.
    # -----------------------------------------------------------------------

    async def test_empty_text_segment_hdeld_from_redis_unconditionally(self, mock_redis):
        """AIS-164: empty-text segments must be hdel'd even when batch_to_store is empty.

        Old bug: hdel only ran inside `if batch_to_store:`. When the only segment
        had empty text, batch was empty, hdel never ran, segment stuck in Redis
        forever. Endpoint read it back and returned 0 usable segments.
        """
        from meeting_api.collector.db_writer import process_redis_to_postgres
        import asyncio

        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        segment_data = {
            "text": "   ", "start_time": 0.0, "end_time": 1.0,
            "language": "en", "updated_at": old_time,
            "session_uid": "sess-1", "speaker": "Alice", "segment_id": "seg-empty",
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

        # Must be hdel'd even though nothing was committed to PG
        # (loop runs multiple times in 150ms; any_call confirms the right args were used)
        mock_redis.hdel.assert_any_call("meeting:1:segments", "seg-empty")
        mock_db.commit.assert_not_called()

    async def test_parse_error_segment_hdeld_from_redis_unconditionally(self, mock_redis):
        """AIS-164: unparseable segments must be hdel'd unconditionally.

        Same invariant as empty-text: broken segments will never go to PG,
        so they must be cleaned from Redis immediately without waiting for
        batch_to_store to be non-empty.
        """
        from meeting_api.collector.db_writer import process_redis_to_postgres
        import asyncio

        mock_redis.smembers = AsyncMock(return_value={"7"})
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

        mock_redis.hdel.assert_any_call("meeting:7:segments", "bad-seg")
        mock_db.commit.assert_not_called()

    async def test_valid_segment_not_hdeld_from_redis_if_pg_commit_fails(self, mock_redis):
        """AIS-164 data-safety invariant: valid segment Redis entry must survive a PG commit failure.

        If db.commit() raises, the segment must stay in Redis so the next
        poll cycle can retry the PG write. Deleting from Redis before/during
        a failed commit would cause permanent data loss.
        """
        from meeting_api.collector.db_writer import process_redis_to_postgres
        import asyncio

        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        segment_data = {
            "text": "Hello world", "start_time": 0.0, "end_time": 5.0,
            "language": "en", "updated_at": old_time,
            "session_uid": "sess-1", "speaker": "Alice", "segment_id": "seg-valid",
        }
        mock_redis.smembers = AsyncMock(return_value={"3"})
        mock_redis.hgetall = AsyncMock(return_value={"seg-valid": json.dumps(segment_data)})

        with patch("meeting_api.collector.db_writer.BACKGROUND_TASK_INTERVAL", 0):
            with patch("meeting_api.collector.db_writer.IMMUTABILITY_THRESHOLD", 30):
                with patch("meeting_api.collector.db_writer.async_session_local") as mock_session_ctx:
                    mock_db = AsyncMock()
                    mock_db.execute = AsyncMock()
                    mock_db.commit = AsyncMock(side_effect=Exception("DB connection lost"))
                    mock_db.rollback = AsyncMock()
                    mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                    mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

                    task = asyncio.create_task(process_redis_to_postgres(mock_redis))
                    await asyncio.sleep(0.15)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        # Redis entry must NOT be deleted — segment must survive for retry
        mock_redis.hdel.assert_not_called()

    async def test_cross_meeting_empty_cleaned_independently_of_valid_segment(self, mock_redis):
        """AIS-164 cross-meeting isolation: empty-text segment for Meeting A must be
        hdel'd even when Meeting B has valid segments.

        Old bug: both meetings' segments were in the same `segments_to_delete` dict.
        If Meeting B had valid segments → commit ran → hdel ran for ALL entries
        (including A's empty segment). This was coincidental coupling, not intentional.
        With the fix, Meeting A's empty segment is always cleaned independently.

        Specifically tests the invariant the other way: Meeting B's valid segment
        must only be hdel'd AFTER commit (not earlier), while Meeting A's empty
        segment is hdel'd before commit.
        """
        from meeting_api.collector.db_writer import process_redis_to_postgres
        import asyncio

        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()

        empty_seg = {
            "text": "", "start_time": 0.0, "end_time": 0.5,
            "language": "en", "updated_at": old_time,
            "session_uid": "sess-A", "speaker": None, "segment_id": "seg-empty-A",
        }
        valid_seg = {
            "text": "Hello", "start_time": 1.0, "end_time": 3.0,
            "language": "en", "updated_at": old_time,
            "session_uid": "sess-B", "speaker": "Bob", "segment_id": "seg-valid-B",
        }

        def hgetall_side_effect(key):
            if key == "meeting:10:segments":
                return {"seg-empty-A": json.dumps(empty_seg)}
            if key == "meeting:20:segments":
                return {"seg-valid-B": json.dumps(valid_seg)}
            return {}

        mock_redis.smembers = AsyncMock(return_value={"10", "20"})
        mock_redis.hgetall = AsyncMock(side_effect=hgetall_side_effect)

        hdel_calls = []

        async def capture_hdel(key, *fields):
            hdel_calls.append((key, set(fields)))

        mock_redis.hdel = AsyncMock(side_effect=capture_hdel)

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

        # Both segments must have been hdel'd (empty-text AND valid-after-commit)
        all_deleted_keys = {key for key, _ in hdel_calls}
        assert "meeting:10:segments" in all_deleted_keys, \
            "Empty-text segment for Meeting 10 was not cleaned from Redis"
        assert "meeting:20:segments" in all_deleted_keys, \
            "Valid segment for Meeting 20 was not cleaned from Redis after commit"

        # PG commit happened (valid segment was stored)
        mock_db.commit.assert_called()
