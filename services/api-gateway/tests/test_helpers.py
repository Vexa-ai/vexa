"""Unit tests for pure helper functions in api-gateway main.py.

Tests _format_ts and CORS config parsing without running the server.
"""
import os
import pytest
from main import _format_ts, CORS_ORIGINS


class TestFormatTimestamp:
    def test_zero_seconds(self):
        assert _format_ts(0) == "00:00"

    def test_under_one_minute(self):
        assert _format_ts(45) == "00:45"

    def test_one_minute(self):
        assert _format_ts(60) == "01:00"

    def test_minutes_and_seconds(self):
        assert _format_ts(125) == "02:05"

    def test_one_hour(self):
        assert _format_ts(3600) == "01:00:00"

    def test_hour_minutes_seconds(self):
        assert _format_ts(3661) == "01:01:01"

    def test_float_input_truncates(self):
        assert _format_ts(90.7) == "01:30"

    def test_non_numeric_returns_zero(self):
        assert _format_ts("not_a_number") == "00:00"

    def test_large_value(self):
        assert _format_ts(86400) == "24:00:00"


class TestCorsOrigins:
    def test_cors_origins_is_list(self):
        assert isinstance(CORS_ORIGINS, list)

    def test_cors_origins_no_empty_strings(self):
        for origin in CORS_ORIGINS:
            assert origin.strip() == origin
            assert len(origin) > 0
