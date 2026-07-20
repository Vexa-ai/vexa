"""Authenticated-bot spawn wiring (#724 C2) + per-identity serialization (#725 C2).

Drives the SHIPPED ``request_bot`` over the in-memory fakes, offline. The deployment-scoped
``BOT_AUTHENTICATED`` knob makes every stock ``POST /bots`` spawn carry the sealed invocation.v1
auth block; without the knob the invocation carries none of it (the anonymous flow untouched).
Config gaps refuse loud (503-shaped) before any DB write; a second concurrent spawn against the
same stored session is refused with a typed 409 naming the conflicting meeting.
"""
from __future__ import annotations

import json

import pytest

from meeting_api.bot_spawn import build_invocation, mint_meeting_token, request_bot
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.invocation import conforms_invocation
from meeting_api.bot_spawn.ports import AuthSessionBusy, AuthSessionNotConfigured

SECRET = "test-admin-token"
USER = 7

AUTH_FIELDS = ("authenticated", "userdataS3Path", "s3Endpoint", "s3Bucket", "s3AccessKey", "s3SecretKey")


def _set_auth_env(monkeypatch, **overrides):
    env = {
        "BOT_AUTHENTICATED": "true",
        "BOT_USERDATA_S3_PATH": "userdata/bot-identity-1",
        "BOT_S3_ENDPOINT": "http://minio:9000",
        "BOT_S3_BUCKET": "vexa",
        "BOT_S3_ACCESS_KEY": "userdata-key",
        "BOT_S3_SECRET_KEY": "userdata-secret",
        "TRANSCRIPTION_SERVICE_URL": "https://stt.vexa.ai",
        "TRANSCRIPTION_SERVICE_TOKEN": "tok-test",
    }
    env.update(overrides)
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


async def _spawn(repo, runtime, native_id="abc-defg-hij"):
    return await request_bot(
        repo, runtime, user_id=USER, platform="google_meet",
        native_meeting_id=native_id, bot_name="VexaBot",
        redis_url="redis://redis:6379/0", meeting_api_url="http://meeting-api:8080",
        token_secret=SECRET,
    )


# ── unit: build_invocation carries the sealed auth block ─────────────────────────────────────────

def test_build_invocation_carries_auth_block():
    token = mint_meeting_token(1, USER, "google_meet", "abc-defg-hij", secret=SECRET)
    base = dict(meeting_id=1, platform="google_meet",
                meeting_url="https://meet.google.com/abc-defg-hij", bot_name="VexaBot",
                token=token, native_meeting_id="abc-defg-hij", connection_id="conn-1",
                redis_url="redis://redis:6379/0")
    inv = build_invocation(**base, authenticated=True, userdata_s3_path="userdata/id-1",
                           s3_endpoint="http://minio:9000", s3_bucket="vexa",
                           s3_access_key="k", s3_secret_key="s")
    conforms_invocation(inv)
    assert inv["authenticated"] is True
    assert inv["userdataS3Path"] == "userdata/id-1"
    assert inv["s3Endpoint"] == "http://minio:9000"
    assert inv["s3Bucket"] == "vexa"
    # negative control: without the params, NO auth field ships (None-stripped).
    plain = build_invocation(**base)
    assert not any(f in plain for f in AUTH_FIELDS)


# ── flow: the deployment knob populates every spawn ──────────────────────────────────────────────

async def test_knob_populates_stock_post_bots_spawn(monkeypatch):
    _set_auth_env(monkeypatch)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    await _spawn(repo, runtime)
    inv = json.loads(runtime.specs[0]["env"]["VEXA_BOT_CONFIG"])
    conforms_invocation(inv)
    assert inv["authenticated"] is True
    assert inv["userdataS3Path"] == "userdata/bot-identity-1"
    assert inv["s3AccessKey"] == "userdata-key"
    assert inv["s3SecretKey"] == "userdata-secret"


async def test_knob_off_ships_no_auth_fields(monkeypatch):
    """Base control: without the knob the invocation is the anonymous shape — zero auth fields."""
    _set_auth_env(monkeypatch, BOT_AUTHENTICATED=None)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    await _spawn(repo, runtime)
    inv = json.loads(runtime.specs[0]["env"]["VEXA_BOT_CONFIG"])
    assert not any(f in inv for f in AUTH_FIELDS)


async def test_incomplete_config_refuses_before_any_db_write(monkeypatch):
    _set_auth_env(monkeypatch, BOT_S3_BUCKET=None)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    with pytest.raises(AuthSessionNotConfigured):
        await _spawn(repo, runtime)
    assert repo._meetings == {}          # refused BEFORE the meeting-row insert
    assert runtime.specs == []           # nothing spawned


# ── per-identity serialization (#725 C2): one stored session, one live bot ───────────────────────

async def test_second_concurrent_authenticated_spawn_refused(monkeypatch):
    _set_auth_env(monkeypatch)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    first = await _spawn(repo, runtime, native_id="aaa-aaaa-aaa")
    with pytest.raises(AuthSessionBusy) as exc:
        await _spawn(repo, runtime, native_id="bbb-bbbb-bbb")   # different meeting, same identity
    assert exc.value.conflicting_meeting_id == first["id"]      # refusal names the conflict
    assert str(first["id"]) in str(exc.value)
    assert len(runtime.specs) == 1                              # exactly one bot ran


async def test_terminal_meeting_releases_the_identity(monkeypatch):
    """Negative control: once the first meeting is terminal, the next spawn proceeds — the
    serialization gate holds only while the session is actually in use."""
    _set_auth_env(monkeypatch)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    first = await _spawn(repo, runtime, native_id="aaa-aaaa-aaa")
    repo._meetings[first["id"]]["status"] = "completed"
    second = await _spawn(repo, runtime, native_id="bbb-bbbb-bbb")
    assert second["id"] != first["id"]
    assert len(runtime.specs) == 2


async def test_anonymous_spawns_do_not_serialize(monkeypatch):
    """Anonymous flow untouched: with the knob off, concurrent spawns of different meetings
    never hit the identity gate."""
    _set_auth_env(monkeypatch, BOT_AUTHENTICATED=None)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    await _spawn(repo, runtime, native_id="aaa-aaaa-aaa")
    await _spawn(repo, runtime, native_id="bbb-bbbb-bbb")
    assert len(runtime.specs) == 2
