"""ALLOY: read adapter for ephemeral per-meeting STT scheduler snapshots."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

import jsonschema
from referencing import Registry, Resource


def _load_alloy_stt_telemetry_schema() -> dict:
    """ALLOY: load the shared contract by path in source and production images."""
    rel = (
        Path("meetings")
        / "contracts"
        / "alloy-stt-telemetry.v1"
        / "alloy-stt-telemetry.schema.json"
    )
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"monorepo root with {rel} not found")


_SCHEMA = _load_alloy_stt_telemetry_schema()
_REGISTRY = Registry().with_resource(
    _SCHEMA["$id"],
    Resource.from_contents(_SCHEMA),
)
_SNAPSHOT_VALIDATOR = jsonschema.Draft202012Validator(
    {"$ref": f"{_SCHEMA['$id']}#/$defs/Snapshot"},
    registry=_REGISTRY,
)
_STATUS_RESPONSE_VALIDATOR = jsonschema.Draft202012Validator(
    {"$ref": f"{_SCHEMA['$id']}#/$defs/StatusResponse"},
    registry=_REGISTRY,
)


def _reject_non_finite_json_constant(value: str) -> None:
    """ALLOY: reject JavaScript constants that are not valid JSON numbers."""
    raise ValueError(f"invalid JSON constant: {value}")


def _parse_finite_json_float(value: str) -> float:
    """ALLOY: reject valid JSON exponents that overflow Python's finite float range."""
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"JSON number is not finite: {value}")
    return parsed


def alloy_stt_telemetry_key(meeting_id: int | str) -> str:
    return f"alloy:stt:telemetry:v1:{meeting_id}"


def validate_alloy_stt_status_response(payload: Any) -> None:
    """ALLOY: enforce the sealed StatusResponse at the Meeting API producer seam."""
    _STATUS_RESPONSE_VALIDATOR.validate(payload)


def _is_valid_snapshot(snapshot: Any, meeting_id: int) -> bool:
    # ALLOY: schema owns the payload shape; the adapter owns key/payload identity.
    return (
        isinstance(snapshot, dict)
        and _SNAPSHOT_VALIDATOR.is_valid(snapshot)
        and snapshot["meeting_id"] == str(meeting_id)
    )


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
                snapshot = json.loads(
                    raw,
                    parse_constant=_reject_non_finite_json_constant,
                    parse_float=_parse_finite_json_float,
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not _is_valid_snapshot(snapshot, meeting_id):
                continue
            snapshots.append(snapshot)
        return snapshots
