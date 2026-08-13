"""The mock composition root (Lane A fidelity self-proof) — drives the REAL
``discord_bot.session.MeetingSession`` orchestrator through the REAL adapters (transcript.v1 /
lifecycle.v1 / acts.v1), over ``fakeredis`` + an injected HTTP post, with only the Discord-specific
join/PCM/transcription canned. Proves the control plane end-to-end with no live Discord/redis/HTTP.
"""

import json

import fakeredis.aioredis

from discord_bot.contracts import conforms_lifecycle_event, conforms_transcript_segment
from discord_bot.mock.main import run_mock


def _invocation(**overrides) -> dict:
    inv = {
        "platform": "discord",
        "meetingUrl": "https://discord.com/channels/1/2",
        "botName": "Vexa",
        "nativeMeetingId": "222",
        "redisUrl": "redis://redis:6379",
        "connectionId": "sess-mock-1",
        "meetingApiCallbackUrl": "http://meeting-api/callback",
        "meeting_id": 42,
    }
    inv.update(overrides)
    return inv


async def test_mock_normal_scenario_reaches_completed_with_conforming_events():
    lifecycle_events = []

    async def fake_post(url, headers, body):
        event = json.loads(body)
        conforms_lifecycle_event(event)
        lifecycle_events.append(event)
        return True, 200

    redis_client = fakeredis.aioredis.FakeRedis()
    env = {"VEXA_BOT_CONFIG": json.dumps(_invocation()), "MOCK_SCENARIO": "normal"}

    code = await run_mock(env, redis_client=redis_client, http_post=fake_post)
    assert code == 0

    statuses = [e["status"] for e in lifecycle_events]
    assert statuses[0] == "joining"
    assert "active" in statuses
    assert statuses[-1] == "completed"
    assert lifecycle_events[-1]["completion_reason"] == "stopped"

    # the real transcript.v1 stream carries the canned segments (redis stream, not the fake sink)
    entries = await redis_client.xrange("transcription_segments")
    assert len(entries) >= 1
    for _id, fields in entries:
        payload = json.loads(fields[b"payload"])
        assert payload["type"] == "transcription"
        assert payload["meeting_id"] == 42
        for seg in payload["segments"]:
            conforms_transcript_segment(seg)
            assert seg["text"].startswith("[mock utterance")


async def test_mock_silence_left_alone_scenario_completes_with_left_alone():
    lifecycle_events = []

    async def fake_post(url, headers, body):
        lifecycle_events.append(json.loads(body))
        return True, 200

    redis_client = fakeredis.aioredis.FakeRedis()
    env = {
        "VEXA_BOT_CONFIG": json.dumps(_invocation(automaticLeave={"everyoneLeftTimeout": 1})),
        "MOCK_SCENARIO": "silence-left-alone",
    }

    code = await run_mock(env, redis_client=redis_client, http_post=fake_post)
    assert code == 0
    assert lifecycle_events[-1]["status"] == "completed"
    assert lifecycle_events[-1]["completion_reason"] == "left_alone"


async def test_mock_bad_invocation_is_fatal_and_never_touches_redis():
    async def fake_post(url, headers, body):
        raise AssertionError("must not POST anything for a fatal boot config")

    env = {"VEXA_BOT_CONFIG": "{not json"}
    code = await run_mock(env, redis_client=fakeredis.aioredis.FakeRedis(), http_post=fake_post)
    assert code == 1
