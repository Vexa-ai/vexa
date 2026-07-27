"""ALLOY: shared STT telemetry contract tests for the real Redis reader."""
from __future__ import annotations

import json

import pytest

from meeting_api.collector.alloy_stt_telemetry import AlloySttTelemetryReader


def _snapshot(meeting_id: int, **overrides) -> dict:
    snapshot = {
        "version": 1,
        "meeting_id": str(meeting_id),
        "native_meeting_id": "contract-room",
        "updated_at_ms": 10_000,
        "active_requests": 1,
        "active_audio_sec": 2.5,
        "waiting_channels": 1,
        "queued_audio_sec": 1.25,
        "latest_captured_audio_end_ms": 10_000,
        "latest_processed_audio_end_ms": 8_000,
        "lag_sec": 2,
        "rtf_ema": 0.75,
        "processed_windows": 4,
        "superseded_windows": 1,
        "last_error": None,
    }
    snapshot.update(overrides)
    return snapshot


class StubRedis:
    def __init__(self, rows: list[str]):
        self._rows = rows

    async def mget(self, _keys):
        return self._rows


@pytest.mark.asyncio
async def test_reader_omits_unknown_property_and_keeps_valid_neighbor():
    invalid = _snapshot(41, unknown_contract_field=True)
    valid = _snapshot(42)
    reader = AlloySttTelemetryReader(
        StubRedis([json.dumps(invalid), json.dumps(valid)]),
    )

    assert await reader.read_owned([41, 42]) == [valid]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "{not-json",
        json.dumps(_snapshot(41, version=2)),
        json.dumps(_snapshot(99)),
        json.dumps(_snapshot(41, active_audio_sec=float("nan"))),
        json.dumps(_snapshot(41, active_audio_sec=float("inf"))),
        json.dumps(_snapshot(41, active_audio_sec=float("-inf"))),
        json.dumps(
            _snapshot(41, active_audio_sec="__JSON_NUMBER__"),
        ).replace('"__JSON_NUMBER__"', "1e400"),
    ],
    ids=[
        "malformed-json",
        "unsupported-version",
        "mismatched-meeting-id",
        "nan",
        "infinity",
        "negative-infinity",
        "overflowing-json-number",
    ],
)
async def test_reader_omits_invalid_row_and_keeps_valid_neighbor(invalid):
    valid = _snapshot(42)
    reader = AlloySttTelemetryReader(
        StubRedis([invalid, json.dumps(valid)]),
    )

    assert await reader.read_owned([41, 42]) == [valid]
