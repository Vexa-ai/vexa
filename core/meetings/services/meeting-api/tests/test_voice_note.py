"""A voice note: a meeting with no platform to join, whose audio the person streams (#1783).

``POST /bots {"platform": "voice_note"}`` makes the meeting row and spawns the bot with an
invocation.v1 ``voiceNote`` block and no meeting URL; ``WS /voice-notes/{id}/stream`` takes the
person's capture.v1 audio frames into the note's Redis stream, refuses anything else with a typed
close, caps the note's length, and on Stop appends the end marker the bot finalizes on.
"""
from __future__ import annotations

import json
import struct

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.voice_notes import (
    CLOSE_BAD_FRAME,
    CLOSE_CAP_REACHED,
    CLOSE_NOT_FOUND,
    InMemoryAudioStreamWriter,
)

SECRET = "test-admin-token"
USER = {"x-user-id": "7"}


def frame(samples: int, ts: float = 1_760_000_000_000.0, track: int = 0) -> bytes:
    return struct.pack("<id", track, ts) + struct.pack(f"<{samples}f", *([0.01] * samples))


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt:8000/v1/audio/transcriptions")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "stt-token")
    monkeypatch.setenv("VOICE_NOTE_MAX_DURATION_SEC", "2")
    runtime = FakeRuntimeClient()
    writer = InMemoryAudioStreamWriter()
    client = TestClient(create_app(meeting_repo=InMemoryMeetingRepo(), runtime=runtime,
                                   token_secret=SECRET, voice_note_writer=writer))
    return client, runtime, writer


def _start(client) -> dict:
    resp = client.post("/bots", headers=USER, json={"platform": "voice_note"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_voice_note_spawns_a_bot_with_no_meeting_to_join(env):
    client, runtime, _ = env
    body = _start(client)
    assert body["platform"] == "voice_note"
    assert body["native_meeting_id"].startswith("vn-")
    inv = json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])
    assert inv["platform"] == "voice_note"
    assert inv["meetingUrl"] is None
    assert inv["voiceNote"]["audioStream"] == f"voice_note:meeting:{inv['meeting_id']}:audio"
    assert inv["voiceNote"]["maxDurationSec"] == 2
    assert inv["recordingEnabled"] is False


def test_a_voice_note_takes_a_speaker_name_and_refuses_a_meeting_url(env):
    client, runtime, _ = env
    resp = client.post("/bots", headers=USER, json={"platform": "voice_note", "speaker_name": "Ada"})
    assert resp.status_code == 201, resp.text
    assert json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])["voiceNote"]["speakerName"] == "Ada"
    bad = client.post("/bots", headers=USER,
                      json={"platform": "voice_note", "meeting_url": "https://meet.google.com/abc-defg-hij"})
    assert bad.status_code == 422


def test_frames_reach_the_stream_in_order_and_stop_ends_the_note(env):
    client, _, writer = env
    body = _start(client)
    stream = f"voice_note:meeting:{body['id']}:audio"
    with client.websocket_connect(f"/voice-notes/{body['native_meeting_id']}/stream", headers=USER) as ws:
        ready = ws.receive_json()
        assert ready == {"type": "ready", "max_duration_sec": 2, "received_sec": 0.0}
        for i in range(4):
            ws.send_bytes(frame(4000, ts=1_760_000_000_000.0 + i * 250))
        assert ws.receive_json() == {"type": "progress", "received_sec": 1.0}
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json() == {"type": "stopped", "received_sec": 1.0}
    entries = writer.entries[stream]
    assert [("f" in e) for e in entries] == [True, True, True, True, False]
    assert entries[-1] == {"end": "1"}
    assert entries[0]["f"] == frame(4000)


def test_the_note_is_the_callers_own(env):
    client, _, _ = env
    body = _start(client)
    with client.websocket_connect(f"/voice-notes/{body['native_meeting_id']}/stream",
                                  headers={"x-user-id": "8"}) as ws:
        assert ws.receive_json() == {"type": "error", "error": "not_found"}
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == CLOSE_NOT_FOUND


def test_a_frame_that_is_not_capture_audio_is_refused(env):
    client, _, writer = env
    body = _start(client)
    with client.websocket_connect(f"/voice-notes/{body['native_meeting_id']}/stream", headers=USER) as ws:
        ws.receive_json()
        ws.send_bytes(b"not audio at all")
        assert ws.receive_json() == {"type": "error", "error": "bad_frame"}
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == CLOSE_BAD_FRAME
    assert writer.entries == {}


def test_the_cap_ends_the_note_and_counts_across_reconnects(env):
    client, _, writer = env
    body = _start(client)
    path = f"/voice-notes/{body['native_meeting_id']}/stream"
    stream = f"voice_note:meeting:{body['id']}:audio"
    with client.websocket_connect(path, headers=USER) as ws:
        ws.receive_json()
        ws.send_bytes(frame(16000))
        ws.receive_json()  # progress
    with client.websocket_connect(path, headers=USER) as ws:
        assert ws.receive_json()["received_sec"] == 1.0          # the count survived the reconnect
        ws.send_bytes(frame(16000))
        ws.receive_json()  # progress, 2.0 s: exactly the cap
        ws.send_bytes(frame(160))
        assert ws.receive_json() == {"type": "error", "error": "max_duration"}
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == CLOSE_CAP_REACHED
    assert writer.entries[stream][-1] == {"end": "1"}
    assert sum(1 for x in writer.entries[stream] if "f" in x) == 2


def test_a_plain_get_on_the_socket_path_is_told_to_upgrade(env):
    client, _, _ = env
    r = client.get("/voice-notes/vn-0/stream", headers=USER)
    assert r.status_code == 426 and r.headers["upgrade"] == "websocket"


def test_a_ping_keeps_a_paused_note_alive(env):
    client, _, writer = env
    body = _start(client)
    with client.websocket_connect(f"/voice-notes/{body['native_meeting_id']}/stream", headers=USER) as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}
    assert writer.entries[f"voice_note:meeting:{body['id']}:audio"] == [{"alive": "1"}]
