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


def _seed(repo, *, user_id, platform, native, status="active"):
    """Seed a meeting AT a given lifecycle status. The status is load-bearing for the stop path:
    `stopping` may only be written over a status in which the bot reached the meeting (#807)."""
    m = asyncio.run(
        repo.create_meeting(user_id=user_id, platform=platform, native_meeting_id=native, data={})
    )
    sid = f"sess-{m['id']}"
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=sid))
    if status != "requested":
        asyncio.run(repo.update_meeting_status(session_uid=sid, status=status))
    return m


def _seed_active(repo, *, user_id, platform, native):
    return _seed(repo, user_id=user_id, platform=platform, native=native, status="active")


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


# ── #807: stopping a bot that never reached the meeting must not claim it was live ───────────────


def test_stopping_a_pre_active_bot_preserves_the_stage_it_died_in():
    """The producer half of the never-admitted-but-`completed` bug. Writing `stopping` over
    `awaiting_admission` destroyed the only record of the stage, and every downstream reader then
    concluded the bot had been live. The stage must survive the stop; the user-intent signal rides
    in `data` where it always did."""
    for stage in ("requested", "joining", "awaiting_admission"):
        repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
        app = create_app(meeting_repo=repo, command_publisher=pub)
        _seed(repo, user_id=7, platform="google_meet", native=f"m-{stage}", status=stage)

        r = TestClient(app).delete(f"/bots/google_meet/m-{stage}", headers={"x-user-id": "7"})
        assert r.status_code == 200, r.text

        latest = asyncio.run(repo.find_latest(7, "google_meet", f"m-{stage}"))
        assert latest["status"] == stage, (
            f"a stop at {stage} must not overwrite the stage with 'stopping' — that is the evidence "
            f"the terminal classifier needs to tell 'never admitted' from 'was live'"
        )
        assert latest["data"].get("stop_requested") is True, "user intent must still be recorded"


def test_stop_still_moves_a_live_bot_to_stopping():
    """No-regression: for a bot that DID reach the meeting, `stopping` is exactly right."""
    for stage in ("active",):
        repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
        app = create_app(meeting_repo=repo, command_publisher=pub)
        _seed(repo, user_id=7, platform="google_meet", native=f"live-{stage}", status=stage)

        r = TestClient(app).delete(f"/bots/google_meet/live-{stage}", headers={"x-user-id": "7"})
        assert r.status_code == 200, r.text
        latest = asyncio.run(repo.find_latest(7, "google_meet", f"live-{stage}"))
        assert latest["status"] == "stopping"


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


def test_second_delete_never_re_stops_a_pre_active_bot():
    """The stop trigger is one-shot for EVERY stage. Preserving the pre-active stage means the row
    is still findable by `find_active` afterwards, so the guard has to be the user-intent flag —
    otherwise a redelivered DELETE would publish a second leave command and tear the workload down
    again. (The SQL adapter's active set contains `stopping` too, so this was already reachable in
    production for a live bot; the fake's narrower set hid it.)"""
    for stage in ("requested", "awaiting_admission", "active"):
        repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
        app = create_app(meeting_repo=repo, command_publisher=pub)
        _seed(repo, user_id=7, platform="google_meet", native=f"once-{stage}", status=stage)

        first = TestClient(app).delete(f"/bots/google_meet/once-{stage}", headers={"x-user-id": "7"})
        assert first.status_code == 200, first.text
        published = len(pub.published)

        second = TestClient(app).delete(f"/bots/google_meet/once-{stage}", headers={"x-user-id": "7"})
        assert second.status_code == 404, f"{stage}: a redelivered stop must not re-trigger"
        assert len(pub.published) == published, f"{stage}: second DELETE re-published a leave command"
