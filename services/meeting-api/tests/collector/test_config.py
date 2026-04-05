"""Unit tests for collector config -- verifying defaults and env-var parsing."""
import os
import pytest


class TestConfigDefaults:
    """Test that config module exposes correct defaults when env vars are unset."""

    def test_redis_stream_name_default(self):
        from meeting_api.collector.config import REDIS_STREAM_NAME
        assert REDIS_STREAM_NAME == os.environ.get("REDIS_STREAM_NAME", "transcription_segments")

    def test_redis_consumer_group_default(self):
        from meeting_api.collector.config import REDIS_CONSUMER_GROUP
        assert REDIS_CONSUMER_GROUP == os.environ.get("REDIS_CONSUMER_GROUP", "collector_group")

    def test_redis_stream_read_count_is_int(self):
        from meeting_api.collector.config import REDIS_STREAM_READ_COUNT
        assert isinstance(REDIS_STREAM_READ_COUNT, int)
        assert REDIS_STREAM_READ_COUNT > 0

    def test_redis_stream_block_ms_is_int(self):
        from meeting_api.collector.config import REDIS_STREAM_BLOCK_MS
        assert isinstance(REDIS_STREAM_BLOCK_MS, int)
        assert REDIS_STREAM_BLOCK_MS > 0

    def test_consumer_name_is_string(self):
        from meeting_api.collector.config import CONSUMER_NAME
        assert isinstance(CONSUMER_NAME, str)
        assert len(CONSUMER_NAME) > 0

    def test_pending_msg_timeout_positive(self):
        from meeting_api.collector.config import PENDING_MSG_TIMEOUT_MS
        assert PENDING_MSG_TIMEOUT_MS > 0

    def test_background_task_interval_positive(self):
        from meeting_api.collector.config import BACKGROUND_TASK_INTERVAL
        assert isinstance(BACKGROUND_TASK_INTERVAL, int)
        assert BACKGROUND_TASK_INTERVAL > 0

    def test_immutability_threshold_positive(self):
        from meeting_api.collector.config import IMMUTABILITY_THRESHOLD
        assert isinstance(IMMUTABILITY_THRESHOLD, int)
        assert IMMUTABILITY_THRESHOLD > 0

    def test_redis_segment_ttl_positive(self):
        from meeting_api.collector.config import REDIS_SEGMENT_TTL
        assert isinstance(REDIS_SEGMENT_TTL, int)
        assert REDIS_SEGMENT_TTL > 0

    def test_log_level_is_uppercase(self):
        from meeting_api.collector.config import LOG_LEVEL
        assert LOG_LEVEL == LOG_LEVEL.upper()

    def test_api_key_name_set(self):
        from meeting_api.collector.config import API_KEY_NAME
        assert API_KEY_NAME == "X-API-Key"

    def test_redis_host_default(self):
        from meeting_api.collector.config import REDIS_HOST
        assert isinstance(REDIS_HOST, str)

    def test_redis_port_is_int(self):
        from meeting_api.collector.config import REDIS_PORT
        assert isinstance(REDIS_PORT, int)

    def test_speaker_events_stream_defaults(self):
        from meeting_api.collector.config import (
            REDIS_SPEAKER_EVENTS_STREAM_NAME,
            REDIS_SPEAKER_EVENTS_CONSUMER_GROUP,
            REDIS_SPEAKER_EVENT_TTL,
        )
        assert isinstance(REDIS_SPEAKER_EVENTS_STREAM_NAME, str)
        assert isinstance(REDIS_SPEAKER_EVENTS_CONSUMER_GROUP, str)
        assert isinstance(REDIS_SPEAKER_EVENT_TTL, int)
        assert REDIS_SPEAKER_EVENT_TTL > 0
