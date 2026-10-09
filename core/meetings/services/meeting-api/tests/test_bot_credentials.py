"""A meeting bot holds one credential: the MeetingToken minted for its own session.

The bot renders third-party meeting pages, so nothing it carries may act beyond its own meeting.
Its invocation carries no service-tier secret; its token is bound to its session (``session_uid``);
the lifecycle callback and the uploads accept that token for that session only. The internal tier
is still accepted from trusted services. Drives the SHIPPED routes over the in-memory fakes.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn import mint_meeting_token
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.recordings import build_router
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage

SECRET = "test-admin-token"
INTERNAL = "test-internal-secret-0123456789abcdef"
USER = 7


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)


def _spawn(client, runtime, native="cred-1"):
    r = client.post("/bots", headers={"x-user-id": str(USER)},
                    json={"platform": "google_meet", "native_meeting_id": native})
    assert r.status_code == 201, r.text
    return json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])


def _app():
    runtime = FakeRuntimeClient()
    client = TestClient(create_app(meeting_repo=InMemoryMeetingRepo(), runtime=runtime,
                                   token_secret=SECRET, internal_secret=INTERNAL))
    return client, runtime


def _event(conn, status="joining"):
    return {"connection_id": conn, "status": status}


def test_the_invocation_carries_no_service_secret_and_a_session_bound_token():
    client, runtime = _app()
    inv = _spawn(client, runtime)
    assert "internalSecret" not in inv
    assert INTERNAL not in json.dumps(runtime.specs[-1])
    from meeting_api.recordings.service import _verify_meeting_token

    claims = _verify_meeting_token(inv["token"], secret=SECRET)
    assert claims["session_uid"] == inv["connectionId"]
    assert claims["meeting_id"] == inv["meeting_id"]


def test_the_lifecycle_callback_takes_the_sessions_own_token():
    client, runtime = _app()
    inv = _spawn(client, runtime)
    url = "/bots/internal/callback/lifecycle"
    conn = inv["connectionId"]
    assert client.post(url, json=_event(conn)).status_code == 401
    assert client.post(url, json=_event(conn), headers={"Authorization": "Bearer not-a-token"}).status_code == 401
    forged = mint_meeting_token(inv["meeting_id"], USER, "google_meet", "cred-1", secret="another-key",
                                session_uid=conn)
    assert client.post(url, json=_event(conn), headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    ok = client.post(url, json=_event(conn), headers={"Authorization": f"Bearer {inv['token']}"})
    assert ok.status_code == 200, ok.text


def test_a_bots_token_moves_no_other_session():
    client, runtime = _app()
    mine = _spawn(client, runtime, "cred-a")
    theirs = _spawn(client, runtime, "cred-b")
    r = client.post("/bots/internal/callback/lifecycle", json=_event(theirs["connectionId"]),
                    headers={"Authorization": f"Bearer {mine['token']}"})
    assert r.status_code == 401
    # a token minted without a session binding names no session at all
    unbound = mint_meeting_token(theirs["meeting_id"], USER, "google_meet", "cred-b", secret=SECRET)
    r = client.post("/bots/internal/callback/lifecycle", json=_event(theirs["connectionId"]),
                    headers={"Authorization": f"Bearer {unbound}"})
    assert r.status_code == 401


def test_the_internal_tier_is_still_admitted():
    client, runtime = _app()
    inv = _spawn(client, runtime)
    r = client.post("/bots/internal/callback/lifecycle", json=_event(inv["connectionId"]),
                    headers={"x-internal-secret": INTERNAL})
    assert r.status_code == 200, r.text
    r = client.post("/bots/internal/callback/lifecycle", json=_event(inv["connectionId"], "active"),
                    headers={"x-internal-secret": INTERNAL + "x"})
    assert r.status_code == 401


# ── uploads ─────────────────────────────────────────────────────────────────────────────────────

TAPE = b'{"type":"captured_signal_header","v":1}\n'


def _upload_client():
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=1, user_id=USER, session_uid="conn-a")
    repo.seed(meeting_id=1, user_id=USER, session_uid="conn-b")
    storage = InMemoryStorage()
    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret=SECRET))
    return TestClient(app), storage


def _post(client, token, session_uid):
    return client.post(
        "/internal/recordings/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"metadata": json.dumps({"session_uid": session_uid, "media_type": "signal",
                                      "media_format": "jsonl", "part": "captured-signal"})},
        files={"file": ("captured-signal.jsonl", TAPE, "application/x-ndjson")},
    )


def test_an_upload_token_writes_only_its_own_session():
    client, storage = _upload_client()
    token_a = mint_meeting_token(1, USER, "google_meet", "x", secret=SECRET, session_uid="conn-a")
    assert _post(client, token_a, "conn-b").status_code == 401
    assert storage.blobs == {}
    assert _post(client, token_a, "conn-a").status_code == 200


def test_an_upload_token_without_a_meeting_is_refused_not_a_crash():
    import base64

    client, _ = _upload_client()

    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    import hashlib
    import hmac as _hmac

    signing = f"{b64({'alg': 'HS256'})}.{b64({'user_id': USER})}"
    sig = base64.urlsafe_b64encode(_hmac.new(SECRET.encode(), signing.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    assert _post(client, f"{signing}.{sig}", "conn-a").status_code == 401
