"""ALLOY read adapter for ephemeral per-meeting STT scheduler snapshots."""
from __future__ import annotations

import json
import math
from typing import Any, Iterable


def alloy_stt_telemetry_key(meeting_id: int | str) -> str:
    return f"alloy:stt:telemetry:v1:{meeting_id}"


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_non_negative_number(value: Any, *, optional: bool = False) -> bool:
    if optional and value is None:
        return True
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    )


def _is_valid_last_error(value: Any) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and isinstance(value.get("code"), str)
        and bool(value["code"].strip())
        and isinstance(value.get("message"), str)
        and bool(value["message"].strip())
    )


def _is_valid_snapshot(snapshot: Any, meeting_id: int) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if (
        not _is_non_negative_int(snapshot.get("version"))
        or snapshot["version"] != 1
    ):
        return False
    if (
        not isinstance(snapshot.get("meeting_id"), str)
        or snapshot["meeting_id"] != str(meeting_id)
        or not isinstance(snapshot.get("native_meeting_id"), str)
        or not snapshot["native_meeting_id"].strip()
    ):
        return False
    if not all(
        _is_non_negative_int(snapshot.get(field))
        for field in (
            "updated_at_ms",
            "active_requests",
            "waiting_channels",
            "processed_windows",
            "superseded_windows",
        )
    ):
        return False
    if not all(
        _is_non_negative_number(snapshot.get(field))
        for field in (
            "active_audio_sec",
            "queued_audio_sec",
            "lag_sec",
        )
    ):
        return False
    if not all(
        _is_non_negative_number(snapshot.get(field), optional=True)
        for field in (
            "latest_captured_audio_end_ms",
            "latest_processed_audio_end_ms",
            "rtf_ema",
        )
    ):
        return False
    return _is_valid_last_error(snapshot.get("last_error"))


class AlloySttTelemetryReader:
    def __init__(self, redis: Any):
        self._redis = redis

    async def read_owned(self, meeting_ids: Iterable[int]) -> list[dict]:
        ids = [int(meeting_id) for meeting_id in meeting_ids]
        if not ids:
            return []
        raw_rows = await self._redis.mget(
            [alloy_stt_telemetry_key(meeting_id) for meeting_id in ids],
        )
        snapshots: list[dict] = []
        for meeting_id, raw in zip(ids, raw_rows):
            if not raw:
                continue
            try:
                snapshot = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not _is_valid_snapshot(snapshot, meeting_id):
                continue
            snapshots.append(snapshot)
        return snapshots
