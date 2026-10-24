"""A demo meeting: ``POST /demo-meetings`` — the MCP tool ``start_demo_meeting``.

A call room nobody hosts, the person's own listener in it, and the scripted speakers the same bot
plays into the room (invocation.v1 ``call.presenter``) with tokens minted from the deployment's
LiveKit key. The row is marked ``data.demo``, the service authority is never asked, and an account
gets ``DEMO_MEETINGS_PER_DAY`` of them.
"""
from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.calls import Calls, InMemoryCallRooms, LiveKitConfig
from meeting_api.demo import DEMO_SCRIPT, DEMO_SPEAKERS, demo_ended

SECRET = "test-admin-token"
USER = {"x-user-id": "7"}


def _claims(token: str) -> dict:
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


class RefusingAuthority:
    """A service authority that refuses everything: a demo must never ask it."""

    configured = True
    mode = "enforce"
    asked = 0

    async def decide(self, request):  # pragma: no cover — reaching it is the failure
        RefusingAuthority.asked += 1
        raise AssertionError("a demo meeting asked the service authority")


def _client(monkeypatch, *, calls=True, authority=None):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt:8000/v1/audio/transcriptions")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "stt-token")
    monkeypatch.delenv("DEMO_MEETINGS_PER_DAY", raising=False)
    monkeypatch.delenv("DEMO_MAX_DURATION_SEC", raising=False)
    runtime = FakeRuntimeClient()
    rooms = InMemoryCallRooms()
    repo = InMemoryMeetingRepo()
    cfg = LiveKitConfig(url="wss://calls.example.test", api_key="APIkey1", api_secret="s" * 32,
                        internal_url_override="ws://livekit:7880")
    kwargs = {"service_authority": authority} if authority is not None else {}
    app = create_app(meeting_repo=repo, runtime=runtime, token_secret=SECRET,
                     calls=Calls(cfg, rooms) if calls else None, **kwargs)
    return TestClient(app), runtime, rooms, repo


def test_a_demo_is_a_call_with_the_scripted_speakers_and_is_marked(monkeypatch):
    client, runtime, rooms, _ = _client(monkeypatch)
    resp = client.post("/demo-meetings", headers=USER)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    room = body["native_meeting_id"]
    assert body["platform"] == "vexa_call" and room.startswith("call-")
    assert body["data"]["demo"] is True and body["data"]["call"]["demo_script"] == DEMO_SCRIPT
    assert body["demo"]["speakers"] == list(DEMO_SPEAKERS) and body["demo"]["demos_left_today"] == 2
    assert "get_meeting_transcript" in body["instruction"]
    assert rooms.rooms[room]["metadata"]["meeting_id"] == body["id"]
    inv = json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])
    assert inv["platform"] == "vexa_call" and inv["call"]["room"] == room
    assert inv["call"]["maxDurationSec"] == 300
    listener = _claims(inv["call"]["token"])["video"]
    assert listener["hidden"] is True and listener["canPublish"] is False
    presenter = inv["call"]["presenter"]
    assert presenter["script"] == DEMO_SCRIPT
    assert [s["name"] for s in presenter["speakers"]] == list(DEMO_SPEAKERS)
    for s in presenter["speakers"]:
        claims = _claims(s["token"])
        assert claims["name"] == s["name"] and claims["iss"] == "APIkey1"
        assert claims["video"] == {"room": room, "roomJoin": True, "canPublish": True,
                                   "canPublishSources": ["microphone"], "canSubscribe": False,
                                   "canPublishData": False}
        assert claims["exp"] - claims["nbf"] == 600
    # No speaker token reaches the meeting's data.
    assert "token" not in json.dumps(body["data"])


def test_the_service_authority_is_never_asked(monkeypatch):
    RefusingAuthority.asked = 0
    client, _, _, _ = _client(monkeypatch, authority=RefusingAuthority())
    resp = client.post("/demo-meetings", headers=USER)
    assert resp.status_code == 201, resp.text
    assert RefusingAuthority.asked == 0
    assert resp.json()["data"]["service_authority"]["mode"] not in ("enforce", "observe")


def test_an_account_gets_three_a_day(monkeypatch):
    client, runtime, rooms, _ = _client(monkeypatch)
    for left in (2, 1, 0):
        r = client.post("/demo-meetings", headers=USER)
        assert r.status_code == 201, r.text
        assert r.json()["demo"]["demos_left_today"] == left
    spawned = len(runtime.specs)
    fourth = client.post("/demo-meetings", headers=USER)
    assert fourth.status_code == 429
    assert fourth.json()["detail"]["error"] == "demo_limit_reached"
    assert len(runtime.specs) == spawned and len(rooms.rooms) == 3     # nothing made for the refusal
    # Another account has its own three.
    assert client.post("/demo-meetings", headers={"x-user-id": "8"}).status_code == 201


def test_the_limit_is_a_setting_and_zero_turns_demos_off(monkeypatch):
    client, _, _, _ = _client(monkeypatch)
    monkeypatch.setenv("DEMO_MEETINGS_PER_DAY", "0")
    r = client.post("/demo-meetings", headers=USER)
    assert r.status_code == 429 and r.json()["detail"]["limit"] == 0


def test_a_spawn_that_fails_takes_the_room_away(monkeypatch):
    client, runtime, rooms, _ = _client(monkeypatch)
    r = client.post("/demo-meetings", headers=USER, )
    assert r.status_code == 201
    # The person's concurrency cap is honoured: a demo takes a bot slot like any meeting.
    refused = client.post("/demo-meetings", headers={**USER, "x-user-limits": "1"})
    assert refused.status_code == 429, refused.text
    assert len(rooms.rooms) == 1 and len(rooms.deleted) == 1


def test_no_calls_no_demo(monkeypatch):
    client, _, _, _ = _client(monkeypatch, calls=False)
    r = client.post("/demo-meetings", headers=USER)
    assert r.status_code == 503 and r.json()["detail"]["error"] == "demo_not_available"


def test_without_a_person_it_is_refused(monkeypatch):
    client, _, _, _ = _client(monkeypatch)
    assert client.post("/demo-meetings").status_code == 401


@pytest.mark.asyncio
async def test_demo_completed_is_emitted_for_a_demo_only(capsys):
    await demo_ended({"id": 5, "user_id": 7, "platform": "vexa_call", "native_meeting_id": "call-abcd1234",
                      "status": "completed", "data": {"demo": True}})
    await demo_ended({"id": 6, "user_id": 7, "platform": "vexa_call", "data": {}})
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    events = [line for line in lines if line.get("event") == "demo_completed"]
    assert len(events) == 1 and events[0]["meeting_id"] == "5"


def test_the_tool_is_declared_and_its_route_listed():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    manifest = json.loads((root / "mcp.tools.v1.json").read_text())
    tool = next(t for t in manifest["tools"] if t["name"] == "start_demo_meeting")
    assert tool["route"] == {"method": "POST", "path": "/demo-meetings"} and tool["auth"] == "subject"
    routes = json.loads((root / "routes.v1.json").read_text())["routes"]
    assert any(r["method"] == "POST" and r["path"] == "/demo-meetings" for r in routes)
