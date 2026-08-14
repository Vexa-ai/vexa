"""POST/DELETE /bots/{platform}/{native}/speak — acts.v1 voice command bridge."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import wave

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher


def _active(repo: InMemoryMeetingRepo) -> dict:
    meeting = asyncio.run(repo.create_meeting(
        user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij", data={}
    ))
    session = f"session-{meeting['id']}"
    asyncio.run(repo.create_session(meeting_id=meeting["id"], session_uid=session))
    asyncio.run(repo.update_meeting_status(session_uid=session, status="active"))
    return meeting


def _app():
    repo, publisher = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    meeting = _active(repo)
    return TestClient(create_app(meeting_repo=repo, command_publisher=publisher)), publisher, meeting


def test_text_speak_publishes_acts_v1_command():
    client, publisher, meeting = _app()
    response = client.post(
        "/bots/google_meet/abc-defg-hij/speak",
        headers={"x-user-id": "7"},
        json={"text": "Hello from ProfitZ", "voice": "alloy"},
    )
    assert response.status_code == 200, response.text
    channel, raw = publisher.published[-1]
    assert channel == f"bot_commands:meeting:{meeting['id']}"
    assert json.loads(raw) == {"action": "speak", "text": "Hello from ProfitZ", "voice": "alloy"}


def test_pcm_is_normalized_to_self_describing_wav():
    client, publisher, _ = _app()
    pcm = b"\x00\x00\x10\x00" * 20
    response = client.post(
        "/bots/google_meet/abc-defg-hij/speak",
        headers={"x-user-id": "7"},
        json={"audio_base64": base64.b64encode(pcm).decode(), "format": "pcm", "sample_rate": 24000},
    )
    assert response.status_code == 200, response.text
    act = json.loads(publisher.published[-1][1])
    assert act["action"] == "speak_audio"
    with wave.open(io.BytesIO(base64.b64decode(act["audioBase64"])), "rb") as wav:
        assert wav.getframerate() == 24000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.readframes(wav.getnframes()) == pcm


def test_speak_stop_and_validation_failures():
    client, publisher, _ = _app()
    stopped = client.delete(
        "/bots/google_meet/abc-defg-hij/speak", headers={"x-user-id": "7"}
    )
    assert stopped.status_code == 200, stopped.text
    assert json.loads(publisher.published[-1][1]) == {"action": "speak_stop"}

    assert client.post(
        "/bots/google_meet/abc-defg-hij/speak",
        headers={"x-user-id": "7"}, json={"text": "x", "audio_base64": "eA=="},
    ).status_code == 422
    assert client.post(
        "/bots/google_meet/abc-defg-hij/speak",
        headers={"x-user-id": "7"}, json={"audio_base64": "eA==", "format": "mp3"},
    ).status_code == 422
    assert client.post(
        "/bots/google_meet/abc-defg-hij/speak",
        headers={"x-user-id": "7"}, json={"audio_base64": "not base64", "format": "wav"},
    ).status_code == 422


def test_speak_requires_owned_active_meeting_and_identity():
    client, publisher, _ = _app()
    assert client.post(
        "/bots/google_meet/abc-defg-hij/speak", json={"text": "hello"}
    ).status_code == 401
    assert client.post(
        "/bots/google_meet/abc-defg-hij/speak",
        headers={"x-user-id": "8"}, json={"text": "hello"},
    ).status_code == 404
    assert not publisher.published
