"""ALLOY: Explicit real-Redis integration for owner-scoped STT telemetry."""
from __future__ import annotations

import json
import os
import time

import httpx
import pytest
import redis.asyncio as aioredis

from alloy_real_redis_fixture import (
    allocate_high_meeting_ids,
    collision_safe_redis_rows,
)
from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.collector.alloy_stt_telemetry import alloy_stt_telemetry_key
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher


ALLOY_TEST_REDIS_URL = os.environ.get("ALLOY_TEST_REDIS_URL", "").strip()
pytestmark = [
    pytest.mark.alloy_real_redis,
    pytest.mark.skipif(
        not ALLOY_TEST_REDIS_URL,
        reason="ALLOY_TEST_REDIS_URL is required for real-Redis integration",
    ),
]


@pytest.mark.asyncio
async def test_alloy_stt_status_reads_real_redis_only_for_owned_running_meetings():
    redis = aioredis.from_url(
        ALLOY_TEST_REDIS_URL,
        decode_responses=True,
    )
    store = InMemoryTranscriptStore()
    owner_id = 71
    other_id = 72
    (
        owned_meeting_id,
        other_meeting_id,
        ended_meeting_id,
        malformed_meeting_id,
    ) = allocate_high_meeting_ids(4)
    owned_meeting = store.seed_meeting(
        meeting_id=owned_meeting_id,
        user_id=owner_id,
        platform="google_meet",
        native_meeting_id="owned-room",
        status="active",
    )
    other_meeting = store.seed_meeting(
        meeting_id=other_meeting_id,
        user_id=other_id,
        platform="google_meet",
        native_meeting_id="private-room",
        status="active",
    )
    ended_meeting = store.seed_meeting(
        meeting_id=ended_meeting_id,
        user_id=owner_id,
        platform="google_meet",
        native_meeting_id="ended-room",
        status="completed",
    )
    malformed_meeting = store.seed_meeting(
        meeting_id=malformed_meeting_id,
        user_id=owner_id,
        platform="google_meet",
        native_meeting_id="malformed-room",
        status="active",
    )

    def snapshot(meeting_id: int, native_id: str) -> dict:
        return {
            "version": 1,
            "meeting_id": str(meeting_id),
            "native_meeting_id": native_id,
            "updated_at_ms": int(time.time() * 1000),
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

    malformed = snapshot(malformed_meeting, "malformed-room")
    del malformed["queued_audio_sec"]
    rows = {
        alloy_stt_telemetry_key(owned_meeting): json.dumps(
            snapshot(owned_meeting, "owned-room"),
        ),
        alloy_stt_telemetry_key(other_meeting): json.dumps(
            snapshot(other_meeting, "private-room"),
        ),
        alloy_stt_telemetry_key(ended_meeting): json.dumps(
            snapshot(ended_meeting, "ended-room"),
        ),
        alloy_stt_telemetry_key(malformed_meeting): json.dumps(malformed),
    }
    try:
        async with collision_safe_redis_rows(redis, rows, ttl_sec=60):
            app = create_app(
                transcript_store=store,
                alloy_stt_telemetry_redis=redis,
                meeting_repo=InMemoryMeetingRepo(),
                runtime=FakeRuntimeClient(),
                command_publisher=InMemoryCommandPublisher(),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    "/alloy/stt/status",
                    headers={"x-user-id": str(owner_id)},
                )

            assert response.status_code == 200
            body = response.json()
            assert body["enabled"] is True
            assert body["available"] is True
            returned_ids = {row["meeting_id"] for row in body["meetings"]}
            assert returned_ids == {str(owned_meeting)}
            assert body["meetings"][0]["native_meeting_id"] == "owned-room"
            assert str(other_meeting) not in returned_ids
            assert str(ended_meeting) not in returned_ids
    finally:
        await redis.aclose()
