"""Join-failure screenshots — ``POST /internal/join-evidence/screenshot``.

Founder, 1 Nov 2026: he asked to see the screen a prod bot was refused on and nobody could show it;
the checkpoint screenshots died with the pod. A refused join must leave exactly one retrievable
PNG, under its own keyspace, never as a recording — and the route must refuse what is not a PNG,
what is too large, and anyone who is not that session's bot.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import mint_meeting_token
from meeting_api.join_screenshots import (
    JOIN_SCREENSHOT_MAX_BYTES,
    build_router,
    delete_meeting_screenshots,
    screenshot_key,
)
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage

SECRET = "test-admin-token"
USER = 7
MEETING_ID = 1
SESSION_UID = "conn-abc"
PNG = b"\x89PNG\r\n\x1a\n" + b"refused-screen"


def _setup():
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    repo.seed(meeting_id=2, user_id=USER, session_uid="conn-other")
    storage = InMemoryStorage()
    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret=SECRET))
    return repo, storage, TestClient(app)


def _token(meeting_id=MEETING_ID, session_uid=SESSION_UID):
    return mint_meeting_token(meeting_id, USER, "google_meet", "abc", secret=SECRET, session_uid=session_uid)


def _post(client, data=PNG, *, session_uid=SESSION_UID, token=None, ctype="image/png"):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post(
        "/internal/join-evidence/screenshot",
        headers=headers,
        files={"file": ("join-failure.png", data, ctype)},
        data={"session_uid": session_uid},
    )


async def test_a_refused_join_leaves_one_retrievable_png():
    repo, storage, client = _setup()
    r = _post(client, token=_token())
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    assert key == screenshot_key(meeting_id=MEETING_ID, session_uid=SESSION_UID) == "join-evidence/1/conn-abc.png"
    assert await storage.list("join-evidence/") == [key]
    assert await storage.get(key) == PNG
    # Evidence, not a recording: nothing folds into the meeting's recordings.
    assert await repo.list_meeting_recordings(USER) == []


async def test_a_second_upload_for_the_same_attempt_replaces_it():
    _, storage, client = _setup()
    assert _post(client, token=_token()).status_code == 200
    assert _post(client, PNG + b"-later", token=_token()).status_code == 200
    assert await storage.list("join-evidence/") == ["join-evidence/1/conn-abc.png"]


def test_refuses_unauthenticated_and_foreign_tokens():
    _, _, client = _setup()
    assert _post(client).status_code == 401
    assert _post(client, token="not-a-token").status_code == 401
    # A token bound to another session cannot write this one.
    assert _post(client, token=_token(2, "conn-other")).status_code == 401


def test_refuses_non_png_and_oversize():
    _, _, client = _setup()
    assert _post(client, b"GIF89a....", token=_token(), ctype="image/png").status_code == 415
    big = PNG + b"\0" * JOIN_SCREENSHOT_MAX_BYTES
    assert _post(client, big, token=_token()).status_code == 413


def test_refuses_a_session_uid_that_is_not_a_plain_id():
    _, _, client = _setup()
    assert _post(client, session_uid="../../recordings/7", token=_token()).status_code == 422


async def test_unknown_session_is_404_and_stores_nothing(monkeypatch):
    _, storage, client = _setup()
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    assert _post(client, session_uid="conn-missing", token="internal-secret").status_code == 404
    assert await storage.list("join-evidence/") == []


async def test_erasing_the_meeting_removes_its_screenshots_only():
    _, storage, client = _setup()
    assert _post(client, token=_token()).status_code == 200
    assert _post(client, token=_token(2, "conn-other"), session_uid="conn-other").status_code == 200
    deleted = await delete_meeting_screenshots(storage, meeting_id=MEETING_ID)
    assert deleted == ["join-evidence/1/conn-abc.png"]
    assert await storage.list("join-evidence/") == ["join-evidence/2/conn-other.png"]
