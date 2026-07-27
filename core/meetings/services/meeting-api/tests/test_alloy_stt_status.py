"""ALLOY: focused owner-boundary and aggregate tests for STT status."""
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis
import httpx
import jsonschema
import pytest

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.collector.alloy_stt_status import (
    build_alloy_stt_status_response,
)
from meeting_api.collector.alloy_stt_telemetry import (
    validate_alloy_stt_status_response,
)
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher


OWNER_ID = 71
OTHER_ID = 72
RUNNING_STATUSES = ("requested", "joining", "awaiting_admission", "active", "stopping")


def _snapshot(
    meeting_id: int,
    native_meeting_id: str,
    *,
    updated_at_ms: int,
    **overrides: Any,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "version": 1,
        "meeting_id": str(meeting_id),
        "native_meeting_id": native_meeting_id,
        "updated_at_ms": updated_at_ms,
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


class RecordingOwnerStore(InMemoryTranscriptStore):
    def __init__(self):
        super().__init__()
        self.list_meetings_calls = 0
        self.list_owned_meeting_ids_calls: list[tuple[int, tuple[str, ...]]] = []

    async def list_meetings(self, *args, **kwargs):
        self.list_meetings_calls += 1
        return await super().list_meetings(*args, **kwargs)

    async def list_owned_meeting_ids(
        self,
        user_id: int,
        *,
        statuses: tuple[str, ...],
    ) -> list[int]:
        self.list_owned_meeting_ids_calls.append((user_id, statuses))
        return await super().list_owned_meeting_ids(user_id, statuses=statuses)


class RecordingRedis:
    def __init__(self, inner):
        self.inner = inner
        self.mget_keys: list[str] | None = None

    async def mget(self, keys):
        self.mget_keys = list(keys)
        return await self.inner.mget(keys)


class FailingRedis:
    def __init__(self):
        self.calls = 0

    async def mget(self, _keys):
        self.calls += 1
        raise ConnectionError("redis unavailable")


def _app(store, telemetry_redis):
    return create_app(
        transcript_store=store,
        alloy_stt_telemetry_redis=telemetry_redis,
        meeting_repo=InMemoryMeetingRepo(),
        runtime=FakeRuntimeClient(),
        command_publisher=InMemoryCommandPublisher(),
    )


def _assert_status_response_conforms(payload: dict[str, Any]) -> None:
    validate_alloy_stt_status_response(payload)


@pytest.mark.asyncio
async def test_status_reads_only_owner_running_ids_and_skips_invalid_neighbors(
    monkeypatch,
):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    recording_redis = RecordingRedis(redis)
    store = RecordingOwnerStore()
    now_ms = 10_000
    collector_app = importlib.import_module("meeting_api.collector.app")
    monkeypatch.setattr(
        collector_app,
        "time",
        SimpleNamespace(time=lambda: now_ms / 1_000),
    )

    owned = store.seed_meeting(
        user_id=OWNER_ID,
        platform="google_meet",
        native_meeting_id="owned-room",
        status="active",
        created_at="2026-07-27T08:00:00Z",
    )
    shared_foreign = store.seed_meeting(
        user_id=OTHER_ID,
        platform="google_meet",
        native_meeting_id="shared-room",
        status="active",
        data={"transcript_viewers": [OWNER_ID]},
    )
    workspace_foreign = store.seed_meeting(
        user_id=OTHER_ID,
        platform="google_meet",
        native_meeting_id="workspace-room",
        status="active",
        data={"workspace_id": "team-notes"},
    )
    completed = store.seed_meeting(
        user_id=OWNER_ID,
        platform="google_meet",
        native_meeting_id="completed-room",
        status="completed",
    )
    malformed = store.seed_meeting(
        user_id=OWNER_ID,
        platform="google_meet",
        native_meeting_id="malformed-room",
        status="joining",
    )
    unsupported = store.seed_meeting(
        user_id=OWNER_ID,
        platform="google_meet",
        native_meeting_id="unsupported-room",
        status="requested",
    )

    values = {
        owned: _snapshot(owned, "owned-room", updated_at_ms=now_ms),
        shared_foreign: _snapshot(
            shared_foreign, "shared-room", updated_at_ms=now_ms
        ),
        workspace_foreign: _snapshot(
            workspace_foreign, "workspace-room", updated_at_ms=now_ms
        ),
        completed: _snapshot(completed, "completed-room", updated_at_ms=now_ms),
        malformed: _snapshot(malformed, "malformed-room", updated_at_ms=now_ms),
        unsupported: _snapshot(
            unsupported, "unsupported-room", updated_at_ms=now_ms, version=2
        ),
    }
    del values[malformed]["queued_audio_sec"]
    for meeting_id, snapshot in values.items():
        await redis.set(
            f"alloy:stt:telemetry:v1:{meeting_id}",
            json.dumps(snapshot),
            ex=30,
        )

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(store, recording_redis)),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/alloy/stt/status",
                headers={
                    "x-user-id": str(OWNER_ID),
                    "x-user-workspaces": "team-notes",
                },
            )

        assert response.status_code == 200
        body = response.json()
        _assert_status_response_conforms(body)
        assert body["enabled"] is True
        assert body["available"] is True
        assert [row["meeting_id"] for row in body["meetings"]] == [str(owned)]
        assert body["aggregate"] == {
            "meetings": 1,
            "active_requests": 1,
            "waiting_channels": 1,
            "queued_audio_sec": 1.25,
            "lag_sec": 2,
            "rtf": 0.75,
            "health": "green",
        }
        assert set(recording_redis.mget_keys or []) == {
            f"alloy:stt:telemetry:v1:{owned}",
            f"alloy:stt:telemetry:v1:{malformed}",
            f"alloy:stt:telemetry:v1:{unsupported}",
        }
        assert str(shared_foreign) not in {
            row["meeting_id"] for row in body["meetings"]
        }
        assert str(workspace_foreign) not in {
            row["meeting_id"] for row in body["meetings"]
        }
        assert str(completed) not in {
            row["meeting_id"] for row in body["meetings"]
        }
        assert store.list_meetings_calls == 0
        assert store.list_owned_meeting_ids_calls == [
            (OWNER_ID, RUNNING_STATUSES),
        ]
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_owner_id_lookup_is_newest_first_with_stable_id_tiebreak():
    store = RecordingOwnerStore()
    newest_low_id = store.seed_meeting(
        meeting_id=10,
        user_id=OWNER_ID,
        platform="google_meet",
        native_meeting_id="newest-low-id",
        status="active",
        created_at="2026-07-27T09:00:00Z",
    )
    newest_high_id = store.seed_meeting(
        meeting_id=11,
        user_id=OWNER_ID,
        platform="google_meet",
        native_meeting_id="newest-high-id",
        status="joining",
        created_at="2026-07-27T09:00:00Z",
    )
    older = store.seed_meeting(
        meeting_id=12,
        user_id=OWNER_ID,
        platform="google_meet",
        native_meeting_id="older",
        status="requested",
        created_at="2026-07-27T08:00:00Z",
    )
    store.seed_meeting(
        user_id=OTHER_ID,
        platform="google_meet",
        native_meeting_id="shared-foreign",
        status="active",
        data={"transcript_viewers": [OWNER_ID]},
        created_at="2026-07-27T10:00:00Z",
    )
    store.seed_meeting(
        user_id=OWNER_ID,
        platform="google_meet",
        native_meeting_id="completed",
        status="completed",
        created_at="2026-07-27T11:00:00Z",
    )

    assert await store.list_owned_meeting_ids(
        OWNER_ID,
        statuses=RUNNING_STATUSES,
    ) == [newest_high_id, newest_low_id, older]


def test_aggregate_sums_counts_and_uses_max_lag_rtf_and_worst_health():
    from meeting_api.collector.alloy_stt_status import aggregate_alloy_stt_status

    snapshots = [
        _snapshot(
            1,
            "one",
            updated_at_ms=10_000,
            active_requests=1,
            waiting_channels=2,
            queued_audio_sec=1.25,
            lag_sec=4,
            rtf_ema=0.7,
        ),
        _snapshot(
            2,
            "two",
            updated_at_ms=10_000,
            active_requests=2,
            waiting_channels=1,
            queued_audio_sec=2.75,
            lag_sec=7,
            rtf_ema=1.2,
        ),
    ]

    assert aggregate_alloy_stt_status(snapshots, now_ms=10_000) == {
        "meetings": 2,
        "active_requests": 3,
        "waiting_channels": 3,
        "queued_audio_sec": 4.0,
        "lag_sec": 7,
        "rtf": 1.2,
        "health": "amber",
    }


@pytest.mark.parametrize(
    ("age_ms", "overrides", "expected"),
    [
        (3_000, {}, "green"),
        (3_001, {}, "amber"),
        (5_000, {}, "amber"),
        (5_001, {}, "red"),
        (0, {"lag_sec": 4.999}, "green"),
        (0, {"lag_sec": 5}, "amber"),
        (0, {"lag_sec": 15}, "amber"),
        (0, {"lag_sec": 15.001}, "red"),
        (0, {"rtf_ema": 1}, "green"),
        (0, {"rtf_ema": 1.001}, "amber"),
        (
            0,
            {"active_requests": 1, "processed_windows": 0},
            "amber",
        ),
        (0, {"last_error": {"code": "stt", "message": "failed"}}, "red"),
        (-1_000, {}, "green"),
    ],
)
def test_aggregate_health_thresholds(age_ms, overrides, expected):
    from meeting_api.collector.alloy_stt_status import aggregate_alloy_stt_status

    health_inputs = {
        "waiting_channels": 0,
        "queued_audio_sec": 0,
        "lag_sec": 0,
        "rtf_ema": None,
        **overrides,
    }
    snapshot = _snapshot(
        1,
        "one",
        updated_at_ms=10_000 - age_ms,
        **health_inputs,
    )

    assert aggregate_alloy_stt_status([snapshot], now_ms=10_000)["health"] == expected


def test_aggregate_without_snapshots_is_muted():
    from meeting_api.collector.alloy_stt_status import aggregate_alloy_stt_status

    assert aggregate_alloy_stt_status([], now_ms=10_000) == {
        "meetings": 0,
        "active_requests": 0,
        "waiting_channels": 0,
        "queued_audio_sec": 0,
        "lag_sec": 0,
        "rtf": None,
        "health": "muted",
    }


def test_aggregate_worst_health_wins_over_green_neighbor():
    from meeting_api.collector.alloy_stt_status import aggregate_alloy_stt_status

    green = _snapshot(
        1,
        "green",
        updated_at_ms=10_000,
        waiting_channels=0,
        lag_sec=0,
        rtf_ema=None,
    )
    red = _snapshot(
        2,
        "red",
        updated_at_ms=10_000,
        last_error={"code": "stt", "message": "failed"},
    )

    assert aggregate_alloy_stt_status(
        [green, red],
        now_ms=10_000,
    )["health"] == "red"


def test_disabled_status_representation_conforms_to_sealed_contract():
    body = build_alloy_stt_status_response(
        enabled=False,
        available=False,
        updated_at_ms=10_000,
        aggregate=None,
        meetings=[],
        error=None,
    )

    assert body == {
        "version": 1,
        "enabled": False,
        "available": False,
        "updated_at_ms": 10_000,
        "aggregate": None,
        "meetings": [],
        "error": None,
    }
    _assert_status_response_conforms(body)


def test_status_response_validator_rejects_missing_required_field():
    malformed = {
        "version": 1,
        "enabled": False,
        "available": False,
        "updated_at_ms": 10_000,
        "aggregate": None,
        "meetings": [],
    }

    with pytest.raises(jsonschema.ValidationError):
        _assert_status_response_conforms(malformed)


@pytest.mark.asyncio
async def test_status_route_fails_loud_when_producer_breaks_sealed_contract(
    monkeypatch,
):
    store = RecordingOwnerStore()
    collector_app = importlib.import_module("meeting_api.collector.app")
    monkeypatch.setattr(
        collector_app,
        "aggregate_alloy_stt_status",
        lambda _snapshots, *, now_ms: {"meetings": 0},
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(store, object())),
        base_url="http://test",
    ) as client:
        with pytest.raises(jsonschema.ValidationError):
            await client.get(
                "/alloy/stt/status",
                headers={"x-user-id": str(OWNER_ID)},
            )


@pytest.mark.asyncio
async def test_redis_failure_degrades_without_breaking_status_response():
    store = RecordingOwnerStore()
    store.seed_meeting(
        user_id=OWNER_ID,
        platform="google_meet",
        native_meeting_id="owned-room",
        status="active",
    )
    redis = FailingRedis()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(store, redis)),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/alloy/stt/status",
            headers={"x-user-id": str(OWNER_ID)},
        )

    assert response.status_code == 200
    body = response.json()
    _assert_status_response_conforms(body)
    assert body == {
        "version": 1,
        "enabled": True,
        "available": False,
        "updated_at_ms": body["updated_at_ms"],
        "aggregate": None,
        "meetings": [],
        "error": {
            "code": "redis_unavailable",
            "message": "STT telemetry is temporarily unavailable",
        },
    }
    assert redis.calls == 1
    assert store.list_meetings_calls == 0
    assert store.list_owned_meeting_ids_calls == [
        (OWNER_ID, RUNNING_STATUSES),
    ]


@pytest.mark.asyncio
async def test_disabled_status_route_is_absent_and_touches_no_owner_dependency():
    store = RecordingOwnerStore()
    app = _app(store, None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/alloy/stt/status")

    assert "/alloy/stt/status" not in app.openapi()["paths"]
    assert response.status_code == 404
    assert store.list_meetings_calls == 0
    assert store.list_owned_meeting_ids_calls == []


@pytest.mark.parametrize(
    ("env", "enabled"),
    [
        ({}, False),
        ({"ALLOY_STT_TELEMETRY": ""}, False),
        ({"ALLOY_STT_TELEMETRY": "0"}, False),
        ({"ALLOY_STT_TELEMETRY": "true"}, False),
        ({"ALLOY_STT_TELEMETRY": "   "}, False),
        ({"ALLOY_STT_TELEMETRY": " 1 "}, True),
    ],
)
def test_meeting_api_telemetry_env_requires_exact_trimmed_one(env, enabled):
    # ALLOY: the composition decision is deterministic under an injected env mapping.
    from meeting_api.__main__ import _resolve_alloy_stt_telemetry_redis

    redis_client = object()
    resolved = _resolve_alloy_stt_telemetry_redis(redis_client, env)

    assert (resolved is redis_client) is enabled
    if not enabled:
        assert resolved is None
