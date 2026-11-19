"""A CALL'S ROOM ENDS WITH ITS MEETING (live app.dev 2026-10-18, founder: "if I stop and start the call
again here, transcription does not start the second time").

The listener left (`left_alone`) and the meeting completed, but the LiveKit room lived on for its
departure timeout — and forever once the person rejoined it. agent-api read that room as the chat's
live call, so starting the call again only took a token for it: no new meeting, no listener, nothing
transcribed. Now the terminal callback marks the room ``ended`` and removes it when nobody is in it.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.calls import Calls, InMemoryCallRooms, LiveKitConfig

SECRET = "test-admin-token"
USER = {"x-user-id": "7"}
SID = "s-0123456789abcdef0123456789abcdef"
INTERNAL = "internal-secret-for-tests"


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt:8000/v1/audio/transcriptions")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "stt-token")
    repo = InMemoryMeetingRepo()
    rooms = InMemoryCallRooms()
    cfg = LiveKitConfig(url="wss://calls.example.test", api_key="APIkey1", api_secret="s" * 32)
    client = TestClient(create_app(meeting_repo=repo, runtime=FakeRuntimeClient(), token_secret=SECRET,
                                   calls=Calls(cfg, rooms), internal_secret=INTERNAL), raise_server_exceptions=False)
    return client, repo, rooms


def _start(client) -> dict:
    resp = client.post("/bots", headers=USER, json={"platform": "vexa_call", "session_id": SID})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _session_uid(repo, meeting_id) -> str:
    (s,) = asyncio.run(repo.list_sessions(meeting_id=meeting_id))
    return s["session_uid"] if isinstance(s, dict) else str(s)


def _drive(client, uid, *statuses, reason="left_alone"):
    for st in statuses:
        body = {"connection_id": uid, "status": st, "timestamp": "2026-10-18T16:25:33.000Z"}
        if st == "completed":
            body["completion_reason"] = reason
        r = client.post("/bots/internal/callback/lifecycle", json=body, headers={"x-internal-secret": INTERNAL})
        assert r.status_code == 200, r.text


def test_the_room_is_marked_ended_and_removed_when_its_meeting_completes(world):
    client, repo, rooms = world
    first = _start(client)
    room = first["native_meeting_id"]
    uid = _session_uid(repo, first["id"])
    _drive(client, uid, "joining", "active")
    assert room in rooms.rooms and not rooms.rooms[room]["metadata"].get("ended")
    _drive(client, uid, "completed")
    assert room in rooms.deleted, "an empty room outliving its meeting is a dead call the chat rejoins"


def test_a_person_still_in_the_room_keeps_it_but_it_reads_ended(world):
    client, repo, rooms = world
    first = _start(client)
    room = first["native_meeting_id"]
    uid = _session_uid(repo, first["id"])
    _drive(client, uid, "joining", "active")
    rooms.rooms[room]["participants"] = [{"identity": "7", "permission": {}},
                                         {"identity": "listener", "permission": {"hidden": True}}]
    _drive(client, uid, "completed", reason="stopped")
    assert room not in rooms.deleted
    meta = rooms.rooms[room]["metadata"]
    assert meta["ended"] is True
    assert meta["owner"] == "7" and meta["session_id"] == SID and meta["meeting_id"] == first["id"]


def test_starting_again_in_the_same_chat_makes_a_fresh_meeting_and_room(world):
    client, repo, rooms = world
    first = _start(client)
    uid = _session_uid(repo, first["id"])
    _drive(client, uid, "joining", "active", "completed")
    second = _start(client)
    assert second["id"] != first["id"] and second["native_meeting_id"] != first["native_meeting_id"]
    assert second["native_meeting_id"] in rooms.rooms
    assert not rooms.rooms[second["native_meeting_id"]]["metadata"].get("ended")
