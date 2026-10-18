"""Removing a person from a live call's room (LiveKit RoomService.RemoveParticipant), as agent-api
does when a participant is removed from the session. L1: httpx.post replaced; no server."""
from __future__ import annotations

import base64
import json

import httpx

from control_plane.livekit_rooms import LiveKit

LK = LiveKit(url="wss://calls.test", api_key="APIkey1", api_secret="s" * 32,
             internal_url="ws://livekit:7880")


def _claims(token: str) -> dict:
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_remove_participant_asks_the_room_service_for_this_room_and_identity(monkeypatch):
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json, auth=headers["Authorization"])
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert LK.remove_participant("call-abc123", "42") is True
    assert seen["url"] == "http://livekit:7880/twirp/livekit.RoomService/RemoveParticipant"
    assert seen["body"] == {"room": "call-abc123", "identity": "42"}
    video = _claims(seen["auth"].split(" ", 1)[1])["video"]
    assert video["room"] == "call-abc123" and video["roomAdmin"] is True


def test_remove_participant_who_is_not_in_the_room_is_not_an_error(monkeypatch):
    def post(url, json=None, headers=None, timeout=None):
        return httpx.Response(404, json={"code": "not_found"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert LK.remove_participant("call-abc123", "42") is False
