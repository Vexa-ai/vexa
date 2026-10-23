"""Deleting a meeting whose bot is still finishing.

`delete_meeting` on a live or stopping meeting answered 409 "artifacts can only be deleted after the
lifecycle is terminal", and the person had to come back later. With `VEXA_MEETING_DELETE_LIVE=stop`
the delete asks the bot to leave, answers 202 "deletion scheduled", and the meeting's transcript and
recordings are deleted when the lifecycle reaches a terminal state. The default, `refuse`, keeps the
0.12 answer (409), so an existing caller sees no change on upgrade.
"""
from __future__ import annotations

import asyncio

import pytest

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.recordings.fakes import InMemoryStorage

OWNER = 7
NATIVE = "private-room"
PREFIX = f"recordings/{OWNER}/9001/sess-41/audio/"


def _world():
    repo = InMemoryMeetingRepo()
    m = asyncio.run(repo.create_meeting(user_id=OWNER, platform="google_meet", native_meeting_id=NATIVE, data={}))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid="sess-41"))
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        meeting_id=m["id"], user_id=OWNER, platform="google_meet", native_meeting_id=NATIVE, status="active",
        data={"recordings": [{"id": 9001, "meeting_id": m["id"], "user_id": OWNER, "session_uid": "sess-41",
                              "status": "completed",
                              "media_files": [{"id": 1, "type": "audio", "format": "wav",
                                               "storage_path": f"{PREFIX}master.wav"}]}],
              "notes": "derived summary"},
        segments=[{"segment_id": "s1", "start": 0, "end": 1, "text": "confidential", "language": "en"}],
    )
    storage = InMemoryStorage()
    storage.blobs[f"{PREFIX}master.wav"] = b"master"
    app = create_app(open_callbacks=True, meeting_repo=repo, transcript_store=store, storage=storage)
    client = TestClient(app, raise_server_exceptions=False)
    for status in ("joining", "active"):
        r = client.post("/bots/internal/callback/lifecycle",
                        json={"connection_id": "sess-41", "status": status, "timestamp": "2026-10-14T10:00:00.000Z"})
        assert r.status_code == 200, r.text
    return m["id"], repo, store, storage, app, client


@pytest.fixture(autouse=True)
def _stop_mode(monkeypatch, request):
    if "refuse_default" in request.node.name:
        monkeypatch.delenv("VEXA_MEETING_DELETE_LIVE", raising=False)
    else:
        monkeypatch.setenv("VEXA_MEETING_DELETE_LIVE", "stop")


def test_refuse_default_answers_409_as_0_12_and_leaves_the_bot_alone():
    """The 0.12.28 contract: DELETE on a meeting whose bot is live answers 409, the bot keeps going,
    nothing is scheduled and nothing is deleted."""
    mid, repo, store, storage, app, client = _world()
    for path in (f"/meetings/google_meet/{NATIVE}", f"/meetings/{mid}"):
        r = client.delete(path, headers={"x-user-id": str(OWNER)})
        assert r.status_code == 409, r.text
        assert "terminal" in r.json()["detail"]
    assert not repo._meetings[mid]["data"].get("stop_requested")
    assert "artifact_deletion" not in store._meetings[mid]["data"]
    assert storage.blobs


def test_delete_while_the_bot_runs_stops_it_and_schedules_the_deletion():
    mid, repo, store, storage, app, client = _world()
    r = client.delete(f"/meetings/google_meet/{NATIVE}", headers={"x-user-id": str(OWNER)})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "deletion_scheduled" and body["id"] == mid
    assert "deleted when" in body["detail"]
    assert repo._meetings[mid]["data"].get("stop_requested") is True, "the bot was asked to leave"
    assert storage.blobs, "nothing is deleted while the bot is still finishing"
    # A second delete while it finishes is the same answer, not an error.
    assert client.delete(f"/meetings/{mid}", headers={"x-user-id": str(OWNER)}).status_code == 202


def test_the_scheduled_deletion_completes_when_the_meeting_ends():
    mid, repo, store, storage, app, client = _world()
    assert client.delete(f"/meetings/google_meet/{NATIVE}", headers={"x-user-id": str(OWNER)}).status_code == 202
    store._meetings[mid]["status"] = "completed"          # one database in production; two fakes here
    r = client.post("/bots/internal/callback/lifecycle",
                    json={"connection_id": "sess-41", "status": "completed", "completion_reason": "stopped",
                          "timestamp": "2026-10-14T10:05:00.000Z"})
    assert r.status_code == 200, r.text
    data = store._meetings[mid]["data"]
    assert data["artifact_deletion"]["state"] == "completed"
    assert "recordings" not in data and "notes" not in data
    assert not storage.blobs


def test_a_meeting_nobody_asked_to_delete_keeps_its_artifacts_when_it_ends():
    mid, repo, store, storage, app, client = _world()
    store._meetings[mid]["status"] = "completed"
    client.post("/bots/internal/callback/lifecycle",
                json={"connection_id": "sess-41", "status": "completed", "completion_reason": "stopped",
                      "timestamp": "2026-10-14T10:05:00.000Z"})
    assert "artifact_deletion" not in store._meetings[mid]["data"]
    assert storage.blobs
