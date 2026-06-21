"""bot_spawn — the POST /bots core flow (invocation.v1 + runtime.v1, eager MeetingSession).

Drives the SHIPPED ``request_bot`` / ``build_router`` over the in-memory fakes, OFFLINE (no DB, no
runtime kernel): the invocation + workload spec conform to the sealed contracts, the MeetingSession
is eager-created keyed by the bot's connectionId, and the quota / dedup seams surface 429 / 409.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import (
    QuotaExceeded,
    SpawnFailed,
    build_invocation,
    build_router,
    build_workload_spec,
    mint_meeting_token,
    request_bot,
)
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.invocation import conforms_invocation, conforms_workload_spec

SECRET = "test-admin-token"
USER = 7
HEADERS = {"x-user-id": str(USER)}


# ── unit: invocation + workload spec conform to the sealed contracts ─────────────────────────────

def test_invocation_conforms_to_invocation_v1():
    token = mint_meeting_token(1, USER, "google_meet", "abc-defg-hij", secret=SECRET)
    inv = build_invocation(
        meeting_id=1, platform="google_meet",
        meeting_url="https://meet.google.com/abc-defg-hij", bot_name="VexaBot",
        token=token, native_meeting_id="abc-defg-hij", connection_id="conn-1",
        redis_url="redis://redis:6379/0",
    )
    conforms_invocation(inv)  # raises on non-conformance
    assert inv["platform"] == "google_meet"
    assert inv["connectionId"] == "conn-1"


def test_workload_spec_conforms_to_runtime_v1():
    inv = build_invocation(
        meeting_id=1, platform="google_meet", meeting_url="https://meet.google.com/x",
        bot_name="VexaBot", token="t", native_meeting_id="x", connection_id="conn-1",
        redis_url="redis://redis:6379/0",
    )
    spec = build_workload_spec(workload_id="mtg-1-conn", invocation=inv,
                               callback_url="http://meeting-api:8080/runtime/callback")
    conforms_workload_spec(spec)
    assert spec["profile"] == "meeting-bot"
    # The invocation rides as the ONE BOT_CONFIG env var (12-factor).
    assert json.loads(spec["env"]["BOT_CONFIG"])["connectionId"] == "conn-1"


def test_meeting_token_roundtrips_under_secret():
    from meeting_api.recordings.service import _verify_meeting_token

    token = mint_meeting_token(42, USER, "google_meet", "abc", secret=SECRET)
    claims = _verify_meeting_token(token, secret=SECRET)
    assert claims["meeting_id"] == 42
    assert claims["user_id"] == USER
    assert claims["scope"] == "transcribe:write"


# ── flow: request_bot eager-creates the session + writes the container back ──────────────────────

async def test_request_bot_eager_creates_session_and_spawns():
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    meeting = await request_bot(
        repo, runtime, user_id=USER, platform="google_meet",
        native_meeting_id="abc-defg-hij", bot_name="VexaBot",
        redis_url="redis://redis:6379/0", meeting_api_url="http://meeting-api:8080",
        token_secret=SECRET,
    )
    assert meeting["status"] == "requested"
    assert meeting["bot_container_id"] == runtime.specs[0]["workloadId"]
    # The eager MeetingSession is keyed by the bot's connectionId.
    assert len(repo.sessions) == 1
    spawned = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert repo.sessions[0]["session_uid"] == spawned["connectionId"]


async def test_request_bot_dedup_raises():
    from meeting_api.bot_spawn import DuplicateMeeting

    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    kw = dict(user_id=USER, platform="google_meet", native_meeting_id="dup",
              redis_url="r", token_secret=SECRET)
    await request_bot(repo, runtime, **kw)
    with pytest.raises(DuplicateMeeting):
        await request_bot(repo, runtime, **kw)


async def test_request_bot_quota_propagates():
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient(quota_exceeded=True)
    with pytest.raises(QuotaExceeded):
        await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                          native_meeting_id="x", redis_url="r", token_secret=SECRET)


# ── route: POST /bots maps outcomes onto HTTP status ─────────────────────────────────────────────

def _client(repo=None, runtime=None):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(build_router(repo or InMemoryMeetingRepo(), runtime or FakeRuntimeClient()))
    return TestClient(app)


def test_post_bots_201(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    client = _client()
    r = client.post("/bots", headers=HEADERS,
                    json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "requested"


def test_post_bots_409_on_duplicate(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    body = {"platform": "google_meet", "native_meeting_id": "dup"}
    assert client.post("/bots", headers=HEADERS, json=body).status_code == 201
    assert client.post("/bots", headers=HEADERS, json=body).status_code == 409


def test_post_bots_429_on_quota(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    client = _client(runtime=FakeRuntimeClient(quota_exceeded=True))
    r = client.post("/bots", headers=HEADERS,
                    json={"platform": "google_meet", "native_meeting_id": "x"})
    assert r.status_code == 429


def test_post_bots_401_without_identity(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    client = _client()
    r = client.post("/bots", json={"platform": "google_meet", "native_meeting_id": "x"})
    assert r.status_code == 401
