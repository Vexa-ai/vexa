"""DELETE /bots/{platform}/{native} — the user-stop route (lifecycle/stop_router).

Drives the SAME shipped ``create_app`` mount with the in-memory fakes: a seeded active meeting is
stopped → the route marks it ``stopping`` + ``stop_requested`` and publishes the bot's ``leave``
command on ``bot_commands:meeting:{id}``. (The bot's terminal lifecycle event — classified by the
existing callback — is exercised by the lifecycle tests; here we assert the trigger.)
"""
from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher


def _seed_active(repo, *, user_id, platform, native):
    m = asyncio.run(
        repo.create_meeting(user_id=user_id, platform=platform, native_meeting_id=native, data={})
    )
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=f"sess-{m['id']}"))
    return m


def test_delete_bots_stops_active_meeting():
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    app = create_app(meeting_repo=repo, command_publisher=pub)
    m = _seed_active(repo, user_id=7, platform="google_meet", native="m1")

    r = TestClient(app).delete("/bots/google_meet/m1", headers={"x-user-id": "7"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "stopping"
    assert body["meeting_id"] == m["id"]

    # the leave command was published on the bot's command channel
    assert pub.published, "no leave command published"
    chan, msg = pub.published[0]
    assert chan == f"bot_commands:meeting:{m['id']}"
    assert json.loads(msg)["action"] == "leave"

    # the meeting row was marked stopping + stop_requested (the user-intent signal)
    latest = asyncio.run(repo.find_latest(7, "google_meet", "m1"))
    assert latest["status"] == "stopping"
    assert latest["data"].get("stop_requested") is True


def test_delete_bots_404_when_no_active_meeting():
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    r = TestClient(create_app(meeting_repo=repo, command_publisher=pub)).delete(
        "/bots/google_meet/nope", headers={"x-user-id": "7"}
    )
    assert r.status_code == 404
    assert not pub.published


def test_delete_bots_401_without_identity():
    r = TestClient(
        create_app(meeting_repo=InMemoryMeetingRepo(), command_publisher=InMemoryCommandPublisher())
    ).delete("/bots/google_meet/m1")
    assert r.status_code == 401
