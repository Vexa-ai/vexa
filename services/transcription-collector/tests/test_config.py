"""Unit tests for config.py -- verifying defaults and env-var parsing."""
import os
import pytest


class TestConfigDefaults:
    """Test that config module exposes correct defaults when env vars are unset."""

    def test_redis_stream_name_default(self):
        import config
        assert config.REDIS_STREAM_NAME == os.environ.get("REDIS_STREAM_NAME", "transcription_segments")

    def test_redis_consumer_group_default(self):
        import config
        assert config.REDIS_CONSUMER_GROUP == os.environ.get("REDIS_CONSUMER_GROUP", "collector_group")

    def test_redis_stream_read_count_is_int(self):
        import config
        assert isinstance(config.REDIS_STREAM_READ_COUNT, int)
        assert config.REDIS_STREAM_READ_COUNT > 0

    def test_redis_stream_block_ms_is_int(self):
        import config
        assert isinstance(config.REDIS_STREAM_BLOCK_MS, int)
        assert config.REDIS_STREAM_BLOCK_MS > 0

    def test_consumer_name_is_string(self):
        import config
        assert isinstance(config.CONSUMER_NAME, str)
        assert len(config.CONSUMER_NAME) > 0

    def test_pending_msg_timeout_positive(self):
        import config
        assert config.PENDING_MSG_TIMEOUT_MS > 0

    def test_background_task_interval_positive(self):
        import config
        assert isinstance(config.BACKGROUND_TASK_INTERVAL, int)
        assert config.BACKGROUND_TASK_INTERVAL > 0

    def test_immutability_threshold_positive(self):
        import config
        assert isinstance(config.IMMUTABILITY_THRESHOLD, int)
        assert config.IMMUTABILITY_THRESHOLD > 0

    def test_redis_segment_ttl_positive(self):
        import config
        assert isinstance(config.REDIS_SEGMENT_TTL, int)
        assert config.REDIS_SEGMENT_TTL > 0

    def test_log_level_is_uppercase(self):
        import config
        assert config.LOG_LEVEL == config.LOG_LEVEL.upper()

    def test_api_key_name_set(self):
        import config
        assert config.API_KEY_NAME == "X-API-Key"

    def test_redis_host_default(self):
        import config
        assert isinstance(config.REDIS_HOST, str)

    def test_redis_port_is_int(self):
        import config
        assert isinstance(config.REDIS_PORT, int)

    def test_speaker_events_stream_defaults(self):
        import config
        assert isinstance(config.REDIS_SPEAKER_EVENTS_STREAM_NAME, str)
        assert isinstance(config.REDIS_SPEAKER_EVENTS_CONSUMER_GROUP, str)
        assert isinstance(config.REDIS_SPEAKER_EVENT_TTL, int)
        assert config.REDIS_SPEAKER_EVENT_TTL > 0
