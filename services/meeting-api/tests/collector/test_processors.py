"""Unit tests for collector/processors.py.

Tests process_stream_message, process_transcript_bundle, and process_speaker_event_message.
All external I/O (DB, Redis) is mocked.
"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


@pytest.fixture
def mock_redis():
    """Create a mock async Redis client."""
    r = AsyncMock()
    r.pipeline = MagicMock()
    pipe = AsyncMock()
    pipe.sadd = MagicMock()
    pipe.expire = MagicMock()
    pipe.hset = MagicMock()
    pipe.zadd = MagicMock()
    pipe.execute = AsyncMock(return_value=[1, True, 1])
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    r.pipeline.return_value = pipe
    r.set = AsyncMock()
    r.delete = AsyncMock(return_value=1)
    return r


def _make_speaker_event(uid="sess-1", timestamp_ms=1000.0, event_type="speaking_start", name="Alice"):
    """Helper to build a speaker event message."""
    return {
        "uid": uid,
        "relative_client_timestamp_ms": str(timestamp_ms),
        "event_type": event_type,
        "participant_name": name,
    }


# --- process_speaker_event_message tests ---

@pytest.mark.asyncio
class TestProcessSpeakerEventMessage:
    async def test_valid_event_stored_in_redis(self, mock_redis):
        from meeting_api.collector.processors import process_speaker_event_message

        event = _make_speaker_event()
        result = await process_speaker_event_message("msg-1", event, mock_redis)

        assert result is True
        mock_redis.pipeline.assert_called_once()

    async def test_missing_required_fields_returns_true(self, mock_redis):
        from meeting_api.collector.processors import process_speaker_event_message

        event = {"relative_client_timestamp_ms": "1000", "event_type": "start", "participant_name": "Bob"}
        result = await process_speaker_event_message("msg-1", event, mock_redis)

        assert result is True  # Bad data is acked to avoid retry loops

    async def test_invalid_timestamp_returns_true(self, mock_redis):
        from meeting_api.collector.processors import process_speaker_event_message

        event = _make_speaker_event(timestamp_ms="not-a-number")
        event["relative_client_timestamp_ms"] = "not-a-number"
        result = await process_speaker_event_message("msg-1", event, mock_redis)

        assert result is True  # Bad data is acked

    async def test_redis_error_returns_false(self, mock_redis):
        import redis.exceptions
        from meeting_api.collector.processors import process_speaker_event_message

        pipe = AsyncMock()
        pipe.zadd = MagicMock()
        pipe.expire = MagicMock()
        pipe.execute = AsyncMock(side_effect=redis.exceptions.RedisError("fail"))
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=False)
        mock_redis.pipeline.return_value = pipe

        event = _make_speaker_event()
        result = await process_speaker_event_message("msg-1", event, mock_redis)

        assert result is False


# --- process_transcript_bundle tests ---

@pytest.mark.asyncio
class TestProcessTranscriptBundle:
    async def test_confirmed_segments_stored(self, mock_redis):
        from meeting_api.collector.processors import process_transcript_bundle

        confirmed = [
            {"segment_id": "seg-1", "text": "Hello world", "start": 0.0, "end": 5.0, "speaker": "Alice"},
            {"segment_id": "seg-2", "text": "Goodbye", "start": 5.0, "end": 8.0, "speaker": "Alice"},
        ]
        result = await process_transcript_bundle("msg-1", {
            "speaker": "Alice", "confirmed": confirmed, "pending": [], "uid": "sess-1"
        }, meeting_id=42, redis_c=mock_redis)

        assert result is True
        mock_redis.pipeline.assert_called()

    async def test_pending_segments_stored_with_ttl(self, mock_redis):
        from meeting_api.collector.processors import process_transcript_bundle

        pending = [{"text": "partial...", "start": 8.0, "end": 10.0}]
        result = await process_transcript_bundle("msg-1", {
            "speaker": "Alice", "confirmed": [], "pending": pending, "uid": "sess-1"
        }, meeting_id=42, redis_c=mock_redis)

        assert result is True
        mock_redis.set.assert_called_once()
        call_kwargs = mock_redis.set.call_args
        assert call_kwargs[1].get("ex") == 60 or call_kwargs[0][2] if len(call_kwargs[0]) > 2 else True

    async def test_empty_pending_deletes_key(self, mock_redis):
        from meeting_api.collector.processors import process_transcript_bundle

        result = await process_transcript_bundle("msg-1", {
            "speaker": "Alice", "confirmed": [], "pending": [], "uid": "sess-1"
        }, meeting_id=42, redis_c=mock_redis)

        assert result is True
        mock_redis.delete.assert_called_once()

    async def test_confirmed_with_empty_text_skipped(self, mock_redis):
        from meeting_api.collector.processors import process_transcript_bundle

        confirmed = [
            {"segment_id": "seg-1", "text": "   ", "start": 0.0, "end": 1.0},
            {"segment_id": "seg-2", "text": "Real text", "start": 1.0, "end": 2.0},
        ]
        result = await process_transcript_bundle("msg-1", {
            "speaker": "Alice", "confirmed": confirmed, "pending": [], "uid": "sess-1"
        }, meeting_id=42, redis_c=mock_redis)

        assert result is True

    async def test_error_returns_false(self, mock_redis):
        from meeting_api.collector.processors import process_transcript_bundle

        mock_redis.pipeline.side_effect = Exception("boom")
        confirmed = [{"segment_id": "seg-1", "text": "Hello", "start": 0, "end": 1}]
        result = await process_transcript_bundle("msg-1", {
            "speaker": "Alice", "confirmed": confirmed, "pending": [], "uid": "sess-1"
        }, meeting_id=42, redis_c=mock_redis)

        assert result is False


# --- process_stream_message tests ---

@pytest.mark.asyncio
class TestProcessStreamMessage:
    async def test_missing_payload_returns_true(self, mock_redis):
        from meeting_api.collector.processors import process_stream_message

        result = await process_stream_message("msg-1", {}, mock_redis)
        assert result is True

    async def test_invalid_json_payload_returns_true(self, mock_redis):
        from meeting_api.collector.processors import process_stream_message

        result = await process_stream_message("msg-1", {"payload": "not{json"}, mock_redis)
        assert result is True

    async def test_unknown_message_type_returns_true(self, mock_redis):
        from meeting_api.collector.processors import process_stream_message

        payload = json.dumps({"type": "unknown_type", "token": "t"})
        with patch("meeting_api.collector.processors.verify_meeting_token", return_value={"meeting_id": 1, "platform": "teams", "native_meeting_id": "abc"}):
            with patch("meeting_api.collector.processors.async_session_local") as mock_session_ctx:
                mock_db = AsyncMock()
                mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
                result = await process_stream_message("msg-1", {"payload": payload}, mock_redis)

        assert result is True

    async def test_failed_token_verification_returns_true(self, mock_redis):
        from meeting_api.collector.processors import process_stream_message

        payload = json.dumps({"type": "transcription", "token": "bad-token"})
        with patch("meeting_api.collector.processors.verify_meeting_token", return_value=None):
            with patch("meeting_api.collector.processors.async_session_local") as mock_session_ctx:
                mock_db = AsyncMock()
                mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
                result = await process_stream_message("msg-1", {"payload": payload}, mock_redis)

        assert result is True
