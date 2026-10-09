"""The MeetingToken has one admit rule, used by both doors a bot calls.

A token admits a request only for the session it is bound to. The lifecycle callback (session =
the event's ``connection_id``) and the recording/tape upload (session = the request's
``session_uid``) both apply ``meeting_token.admit_session``; a token bound to another session, or
to none, is refused at both. Drives the shipped routes over the in-memory fakes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api import create_app, meeting_token
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.recordings import build_router
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage

SECRET = "test-admin-token"
USER = 7


def _hand_made(claims: dict, secret: str = SECRET) -> str:
    """A correctly signed token with exactly ``claims`` — how a token bound to no session looks."""
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    signing = f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64(claims)}"
    sig = hmac.new(secret.encode(), signing.encode(), hashlib.sha256).digest()
    return f"{signing}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


def _unbound(meeting_id: int) -> str:
    return _hand_made({"meeting_id": meeting_id, "user_id": USER, "exp": int(time.time()) + 600})


# ── the module ──────────────────────────────────────────────────────────────────────────────────


def test_a_minted_token_is_bound_to_its_session():
    token = meeting_token.mint_meeting_token(1, USER, "google_meet", "abc", secret=SECRET, session_uid="conn-a")
    claims = meeting_token.verify_meeting_token(token, secret=SECRET)
    assert claims["session_uid"] == "conn-a" and claims["meeting_id"] == 1
    assert meeting_token.admit_session(token, session_uid="conn-a", secret=SECRET)["meeting_id"] == 1


def test_no_token_is_minted_without_a_session():
    with pytest.raises(TypeError):
        meeting_token.mint_meeting_token(1, USER, "google_meet", "abc", secret=SECRET)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        meeting_token.mint_meeting_token(1, USER, "google_meet", "abc", secret=SECRET, session_uid="")


@pytest.mark.parametrize("session_uid", ["conn-b", "", None])
def test_admit_refuses_another_session_or_none(session_uid):
    token = meeting_token.mint_meeting_token(1, USER, "google_meet", "abc", secret=SECRET, session_uid="conn-a")
    with pytest.raises(meeting_token.InvalidMeetingToken):
        meeting_token.admit_session(token, session_uid=session_uid, secret=SECRET)


def test_admit_refuses_a_token_bound_to_no_session():
    with pytest.raises(meeting_token.InvalidMeetingToken, match="no session"):
        meeting_token.admit_session(_unbound(1), session_uid="conn-a", secret=SECRET)


@pytest.mark.parametrize("token", ["", "a.b", "not.a.token", "x.y.z!", "é.é.é"])
def test_a_malformed_token_is_refused_not_a_crash(token):
    with pytest.raises(meeting_token.InvalidMeetingToken):
        meeting_token.verify_meeting_token(token, secret=SECRET)


def test_forged_and_expired_tokens_are_refused():
    forged = meeting_token.mint_meeting_token(1, USER, "google_meet", "abc", secret="another-key", session_uid="c")
    with pytest.raises(meeting_token.InvalidMeetingToken, match="signature"):
        meeting_token.verify_meeting_token(forged, secret=SECRET)
    expired = meeting_token.mint_meeting_token(1, USER, "google_meet", "abc", secret=SECRET, session_uid="c",
                                               ttl_seconds=-10)
    with pytest.raises(meeting_token.InvalidMeetingToken, match="expired"):
        meeting_token.verify_meeting_token(expired, secret=SECRET)


# ── both doors, one rule ────────────────────────────────────────────────────────────────────────


def _upload(client, token, session_uid):
    return client.post(
        "/internal/recordings/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"metadata": json.dumps({"session_uid": session_uid, "media_type": "signal",
                                      "media_format": "jsonl", "part": "captured-signal"})},
        files={"file": ("captured-signal.jsonl", b'{"type":"captured_signal_header","v":1}\n',
                        "application/x-ndjson")},
    )


def test_the_upload_refuses_a_token_bound_to_no_session():
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=1, user_id=USER, session_uid="conn-a")
    storage = InMemoryStorage()
    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret=SECRET))
    r = _upload(TestClient(app), _unbound(1), "conn-a")
    assert r.status_code == 401, r.text
    assert storage.blobs == {}


def test_the_lifecycle_callback_refuses_a_token_bound_to_no_session():
    repo = InMemoryMeetingRepo()
    client = TestClient(create_app(meeting_repo=repo, runtime=FakeRuntimeClient(), token_secret=SECRET))
    r = client.post("/bots/internal/callback/lifecycle", json={"connection_id": "conn-a", "status": "joining"},
                    headers={"Authorization": f"Bearer {_unbound(1)}"})
    assert r.status_code == 401
