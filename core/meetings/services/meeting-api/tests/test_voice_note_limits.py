"""A voice note's audio is paced, its keepalives and text frames are budgeted, and its Redis stream
lives minutes past its last write and goes when the note ends or is deleted (#1783)."""
from __future__ import annotations

import json
import struct

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.voice_notes import (
    CLOSE_RATE_LIMITED,
    END_TTL_SEC,
    InMemoryAudioStreamWriter,
    build_router,
    stream_ttl_sec,
)

SECRET = "test-admin-token"
USER = {"x-user-id": "7"}


def frame(samples: int, ts: float = 1_760_000_000_000.0) -> bytes:
    return struct.pack("<id", 0, ts) + struct.pack(f"<{samples}f", *([0.01] * samples))


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt:8000/v1/audio/transcriptions")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "stt-token")
    monkeypatch.setenv("VOICE_NOTE_MAX_DURATION_SEC", "60")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    writer = InMemoryAudioStreamWriter()
    client = TestClient(create_app(meeting_repo=repo, runtime=runtime, token_secret=SECRET,
                                   voice_note_writer=writer))
    return client, runtime, writer, repo


def _start(client) -> dict:
    resp = client.post("/bots", headers=USER, json={"platform": "voice_note"})
    assert resp.status_code == 201, resp.text
    return resp.json()


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_audio_faster_than_realtime_is_paced(env):
    client, _, writer, repo = env
    body = _start(client)
    clock = FakeClock()
    app = FastAPI()
    app.include_router(build_router(repo, writer, clock=clock, sleep=clock.sleep))
    with TestClient(app).websocket_connect(f"/voice-notes/{body['native_meeting_id']}/stream",
                                           headers=USER) as ws:
        ws.receive_json()
        for i in range(20):                       # 20 s of audio in one burst, no wall time passing
            ws.send_bytes(frame(16000, ts=1_760_000_000_000.0 + i * 1000))
        for _ in range(20):
            ws.receive_json()                     # progress, once a second of audio
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json()["type"] == "stopped"
    # 20 s of audio at no more than 1.5x realtime past a short burst takes wall time.
    assert clock.now >= (20 - 5) / 1.5 - 0.01
    assert sum(1 for e in writer.entries[f"voice_note:meeting:{body['id']}:audio"] if "f" in e) == 20


def test_keepalives_are_written_at_most_once_per_interval(env):
    client, _, writer, _ = env
    body = _start(client)
    with client.websocket_connect(f"/voice-notes/{body['native_meeting_id']}/stream", headers=USER) as ws:
        ws.receive_json()
        for _ in range(10):
            ws.send_text(json.dumps({"type": "ping"}))
            assert ws.receive_json() == {"type": "pong"}
    alive = [e for e in writer.entries[f"voice_note:meeting:{body['id']}:audio"] if "alive" in e]
    assert len(alive) == 1


def test_a_flood_of_text_frames_is_refused(env):
    client, _, writer, _ = env
    body = _start(client)
    with client.websocket_connect(f"/voice-notes/{body['native_meeting_id']}/stream", headers=USER) as ws:
        ws.receive_json()
        for _ in range(60):
            ws.send_text(json.dumps({"type": "nonsense"}))
        seen = []
        with pytest.raises(WebSocketDisconnect) as e:
            while True:
                seen.append(ws.receive_json())
    assert e.value.code == CLOSE_RATE_LIMITED
    assert seen[-1] == {"type": "error", "error": "rate_limited"}
    assert len(seen) < 60


def test_the_stream_outlives_its_last_write_by_minutes_not_the_cap_plus_an_hour(env):
    client, _, writer, _ = env
    body = _start(client)
    stream = f"voice_note:meeting:{body['id']}:audio"
    with client.websocket_connect(f"/voice-notes/{body['native_meeting_id']}/stream", headers=USER) as ws:
        ws.receive_json()
        ws.send_bytes(frame(4000))
        ws.send_text(json.dumps({"type": "ping"}))
        ws.receive_json()
        assert writer.ttls[stream] == stream_ttl_sec() <= 15 * 60
        ws.send_text(json.dumps({"type": "stop"}))
        ws.receive_json()
    assert writer.ttls[stream] == END_TTL_SEC <= 5 * 60


def test_the_stream_is_dropped_when_the_note_ends(env):
    client, runtime, writer, _ = env
    body = _start(client)
    stream = f"voice_note:meeting:{body['id']}:audio"
    with client.websocket_connect(f"/voice-notes/{body['native_meeting_id']}/stream", headers=USER) as ws:
        ws.receive_json()
        ws.send_bytes(frame(4000))
        ws.send_text(json.dumps({"type": "stop"}))
        ws.receive_json()
    assert stream in writer.entries
    inv = json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])
    auth = {"Authorization": f"Bearer {inv['token']}"}
    for status in ("joining", "active"):
        assert client.post("/bots/internal/callback/lifecycle", headers=auth,
                           json={"connection_id": inv["connectionId"], "status": status}).status_code == 200
    assert stream in writer.entries
    assert client.post("/bots/internal/callback/lifecycle", headers=auth,
                       json={"connection_id": inv["connectionId"], "status": "completed",
                             "exit_code": 0, "completion_reason": "stopped"}).status_code == 200
    assert stream not in writer.entries
    assert stream not in writer.counts


def test_the_stream_is_dropped_when_the_meeting_is_deleted():
    store = InMemoryTranscriptStore()
    store.seed_meeting(meeting_id=41, user_id=7, platform="voice_note", native_meeting_id="vn-0123456789ab",
                       status="completed", data={}, segments=[])
    writer = InMemoryAudioStreamWriter()
    stream = "voice_note:meeting:41:audio"
    writer.entries[stream] = [{"f": b"x"}, {"end": "1"}]
    writer.counts[stream] = 4000
    client = TestClient(create_app(transcript_store=store, voice_note_writer=writer),
                        raise_server_exceptions=False)
    assert client.delete("/meetings/41", headers=USER).status_code == 204
    assert stream not in writer.entries and stream not in writer.counts
