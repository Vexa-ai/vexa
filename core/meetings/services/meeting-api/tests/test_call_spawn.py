"""A call: a meeting on the deployment's own LiveKit server (#1783, ADR-0048 decision 6).

``POST /bots {"platform": "vexa_call"}`` creates the room with its transcription notice, makes the
meeting row (the caller owns it) and spawns the listener with a hidden, subscribe-only token. The
token is in the invocation only — never in the meeting's data.
"""
from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.calls import Calls, InMemoryCallRooms, LiveKitConfig

SECRET = "test-admin-token"
USER = {"x-user-id": "7"}
SID = "s-0123456789abcdef0123456789abcdef"


def _claims(token: str) -> dict:
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt:8000/v1/audio/transcriptions")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "stt-token")
    monkeypatch.delenv("CALL_TRANSCRIPTION_SWITCHABLE", raising=False)
    monkeypatch.delenv("CALL_TRANSCRIBE_SHARED_SCREEN", raising=False)
    runtime = FakeRuntimeClient()
    rooms = InMemoryCallRooms()
    cfg = LiveKitConfig(url="wss://calls.example.test", api_key="APIkey1", api_secret="s" * 32,
                        internal_url_override="ws://livekit:7880")
    client = TestClient(create_app(meeting_repo=InMemoryMeetingRepo(), runtime=runtime,
                                   token_secret=SECRET, calls=Calls(cfg, rooms)))
    return client, runtime, rooms


def test_a_call_creates_its_room_with_the_notice_and_spawns_a_hidden_listener(env):
    client, runtime, rooms = env
    resp = client.post("/bots", headers=USER, json={"platform": "vexa_call", "session_id": SID})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    room = body["native_meeting_id"]
    assert body["platform"] == "vexa_call" and room.startswith("call-")
    meta = rooms.rooms[room]["metadata"]
    assert meta["transcribed"] is True and meta["transcription_switchable"] is False
    assert meta["owner"] == "7" and meta["session_id"] == SID
    assert meta["meeting_id"] == body["id"]
    inv = json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])
    assert inv["platform"] == "vexa_call" and inv["meetingUrl"] is None
    assert inv["call"]["url"] == "ws://livekit:7880" and inv["call"]["room"] == room
    assert inv["recordingEnabled"] is False
    video = _claims(inv["call"]["token"])["video"]
    assert video["hidden"] is True and video["canPublish"] is False and video["room"] == room
    data = body.get("data") or {}
    assert data["call"]["room"] == room and data["call"]["url"] == "wss://calls.example.test"
    assert "token" not in json.dumps(data["call"])


def test_a_shared_screen_is_transcribed_by_default_and_the_notice_says_so(env, monkeypatch):
    client, runtime, rooms = env
    resp = client.post("/bots", headers=USER, json={"platform": "vexa_call", "session_id": SID})
    assert resp.status_code == 201, resp.text
    inv = json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])
    assert inv["call"]["screenShareAudio"] is True
    assert rooms.rooms[resp.json()["native_meeting_id"]]["metadata"]["shared_screen_transcribed"] is True
    # The policy switch: off ⇒ the listener never reads a shared screen's audio, and the notice says so.
    monkeypatch.setenv("CALL_TRANSCRIBE_SHARED_SCREEN", "false")
    off = client.post("/bots", headers=USER, json={"platform": "vexa_call", "session_id": SID})
    assert off.status_code == 201, off.text
    inv = json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])
    assert inv["call"]["screenShareAudio"] is False
    assert rooms.rooms[off.json()["native_meeting_id"]]["metadata"]["shared_screen_transcribed"] is False


def test_a_call_refuses_an_id_or_a_url_and_a_bad_session(env):
    client, _, _ = env
    for extra in ({"native_meeting_id": "call-mine"}, {"meeting_url": "https://meet.google.com/abc-defg-hij"},
                  {"session_id": "not-a-session"}):
        resp = client.post("/bots", headers=USER, json={"platform": "vexa_call", **extra})
        assert resp.status_code == 422, (extra, resp.text)


def test_transcription_cannot_be_turned_off_unless_the_deployment_allows_it(env, monkeypatch):
    client, runtime, rooms = env
    off = client.post("/bots", headers=USER, json={"platform": "vexa_call", "transcribe": False})
    assert off.status_code == 422 and "always transcribed" in off.text
    monkeypatch.setenv("CALL_TRANSCRIPTION_SWITCHABLE", "true")
    spawned = len(runtime.specs)
    ok = client.post("/bots", headers=USER, json={"platform": "vexa_call", "session_id": SID, "transcribe": False})
    assert ok.status_code == 201, ok.text
    body = ok.json()
    meta = rooms.rooms[body["native_meeting_id"]]["metadata"]
    assert meta["transcribed"] is False and meta["transcription_switchable"] is True
    # AN UNTRANSCRIBED CALL STARTS NO TRANSCRIBER AT ALL (a private / incognito chat's call): no
    # listener in the room capturing audio, and no meeting row that would keep it.
    assert len(runtime.specs) == spawned
    assert body["id"] is None and body["transcribed"] is False and "meeting_id" not in meta
    assert body["data"]["call"]["room"] == body["native_meeting_id"]


def test_a_call_nobody_joins_ends_within_seconds_not_minutes(env, monkeypatch):
    """Started and cancelled before anyone joined: the listener and the room go within ~30 s."""
    from meeting_api.calls.spawn import DEFAULT_FIRST_JOIN_TIMEOUT_SEC, CallLimits
    monkeypatch.delenv("CALL_FIRST_JOIN_TIMEOUT_SEC", raising=False)
    assert DEFAULT_FIRST_JOIN_TIMEOUT_SEC <= 30
    assert CallLimits.from_env().first_join_timeout_sec == DEFAULT_FIRST_JOIN_TIMEOUT_SEC
    client, runtime, rooms = env
    resp = client.post("/bots", headers=USER, json={"platform": "vexa_call", "session_id": SID})
    room = resp.json()["native_meeting_id"]
    assert rooms.rooms[room]["empty_timeout"] <= 30
    assert json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])["call"]["firstJoinTimeoutSec"] <= 30


def test_no_livekit_means_no_calls(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt:8000/v1/audio/transcriptions")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "stt-token")
    client = TestClient(create_app(meeting_repo=InMemoryMeetingRepo(), runtime=FakeRuntimeClient(),
                                   token_secret=SECRET))
    resp = client.post("/bots", headers=USER, json={"platform": "vexa_call"})
    assert resp.status_code == 503 and "calls_not_deployed" in resp.text


def test_a_call_is_stopped_like_any_meeting(env):
    client, _, _ = env
    body = client.post("/bots", headers=USER, json={"platform": "vexa_call"}).json()
    resp = client.delete(f"/bots/vexa_call/{body['native_meeting_id']}", headers=USER)
    assert resp.status_code in (200, 202), resp.text


def _client_with(monkeypatch, runtime):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt:8000/v1/audio/transcriptions")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "stt-token")
    rooms = InMemoryCallRooms()
    cfg = LiveKitConfig(url="wss://calls.example.test", api_key="APIkey1", api_secret="s" * 32)
    client = TestClient(create_app(meeting_repo=InMemoryMeetingRepo(), runtime=runtime,
                                   token_secret=SECRET, calls=Calls(cfg, rooms)))
    return client, rooms


def test_a_call_over_the_bot_limit_leaves_no_room_behind(monkeypatch):
    client, rooms = _client_with(monkeypatch, FakeRuntimeClient())
    resp = client.post("/bots", headers={**USER, "x-user-limits": "0"}, json={"platform": "vexa_call"})
    assert resp.status_code == 429, resp.text
    assert rooms.rooms == {}
    assert len(rooms.deleted) == 1


def test_a_call_whose_listener_cannot_start_leaves_no_room_behind(monkeypatch):
    client, rooms = _client_with(monkeypatch, FakeRuntimeClient(fail=True))
    resp = client.post("/bots", headers=USER, json={"platform": "vexa_call"})
    assert resp.status_code == 502, resp.text
    assert rooms.rooms == {}


def test_a_started_call_keeps_its_room(monkeypatch):
    client, rooms = _client_with(monkeypatch, FakeRuntimeClient())
    resp = client.post("/bots", headers=USER, json={"platform": "vexa_call"})
    assert resp.status_code == 201, resp.text
    assert list(rooms.rooms) == [resp.json()["native_meeting_id"]] and rooms.deleted == []
