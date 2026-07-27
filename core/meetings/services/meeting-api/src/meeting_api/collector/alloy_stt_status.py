"""ALLOY: server-owned aggregation for ephemeral STT queue snapshots."""
from __future__ import annotations

from typing import Any


_HEALTH_RANK = {
    "muted": 0,
    "green": 1,
    "amber": 2,
    "red": 3,
}


def _snapshot_health(snapshot: dict[str, Any], *, now_ms: int) -> str:
    age_ms = max(0, now_ms - snapshot["updated_at_ms"])
    lag_sec = snapshot["lag_sec"]
    rtf = snapshot["rtf_ema"]
    if snapshot["last_error"] is not None or age_ms > 5_000 or lag_sec > 15:
        return "red"
    if age_ms > 3_000 or lag_sec >= 5 or (rtf is not None and rtf > 1):
        return "amber"
    return "green"


def aggregate_alloy_stt_status(
    snapshots: list[dict[str, Any]],
    *,
    now_ms: int,
) -> dict[str, Any]:
    """ALLOY: reduce validated owner snapshots into the Terminal's global status."""
    if not snapshots:
        return {
            "meetings": 0,
            "active_requests": 0,
            "waiting_channels": 0,
            "queued_audio_sec": 0,
            "lag_sec": 0,
            "rtf": None,
            "health": "muted",
        }

    rtfs = [
        snapshot["rtf_ema"]
        for snapshot in snapshots
        if snapshot["rtf_ema"] is not None
    ]
    health = max(
        (_snapshot_health(snapshot, now_ms=now_ms) for snapshot in snapshots),
        key=_HEALTH_RANK.__getitem__,
    )
    return {
        "meetings": len(snapshots),
        "active_requests": sum(
            snapshot["active_requests"] for snapshot in snapshots
        ),
        "waiting_channels": sum(
            snapshot["waiting_channels"] for snapshot in snapshots
        ),
        "queued_audio_sec": sum(
            snapshot["queued_audio_sec"] for snapshot in snapshots
        ),
        "lag_sec": max(snapshot["lag_sec"] for snapshot in snapshots),
        "rtf": max(rtfs) if rtfs else None,
        "health": health,
    }
