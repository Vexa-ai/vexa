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


def test_invocation_carries_stt_creds_when_provided():
    """The bot can only transcribe if the invocation carries the STT URL+token (the mock-bot/dashboard
    validation found these were dropped). When provided they ride the invocation; when not, they are
    omitted (None-stripped) and the bot joins+captures without transcribing."""
    token = mint_meeting_token(1, USER, "google_meet", "abc-defg-hij", secret=SECRET)
    base = dict(meeting_id=1, platform="google_meet", meeting_url="https://meet.google.com/abc-defg-hij",
                bot_name="VexaBot", token=token, native_meeting_id="abc-defg-hij",
                connection_id="conn-1", redis_url="redis://redis:6379/0")
    inv = build_invocation(**base, transcription_service_url="https://transcription.vexa.ai",
                           transcription_service_token="tok-123")
    conforms_invocation(inv)
    assert inv["transcriptionServiceUrl"] == "https://transcription.vexa.ai"
    assert inv["transcriptionServiceToken"] == "tok-123"
    # absent → omitted, not null
    assert "transcriptionServiceUrl" not in build_invocation(**base)


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

async def test_request_bot_eager_creates_session_and_spawns(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
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


async def test_request_bot_dedup_raises(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    from meeting_api.bot_spawn import DuplicateMeeting

    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    kw = dict(user_id=USER, platform="google_meet", native_meeting_id="dup",
              redis_url="r", token_secret=SECRET)
    await request_bot(repo, runtime, **kw)
    with pytest.raises(DuplicateMeeting):
        await request_bot(repo, runtime, **kw)


async def test_request_bot_quota_propagates(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
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
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    client = _client()
    r = client.post("/bots", headers=HEADERS,
                    json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "requested"


def test_post_bots_409_on_duplicate(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    body = {"platform": "google_meet", "native_meeting_id": "dup"}
    assert client.post("/bots", headers=HEADERS, json=body).status_code == 201
    assert client.post("/bots", headers=HEADERS, json=body).status_code == 409


def test_post_bots_429_on_quota(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    client = _client(runtime=FakeRuntimeClient(quota_exceeded=True))
    r = client.post("/bots", headers=HEADERS,
                    json={"platform": "google_meet", "native_meeting_id": "x"})
    assert r.status_code == 429


def test_post_bots_401_without_identity(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    client = _client()
    r = client.post("/bots", json={"platform": "google_meet", "native_meeting_id": "x"})
    assert r.status_code == 401


def test_post_bots_transcribe_without_stt_fails_loud(monkeypatch):
    """No env TRANSCRIPTION_SERVICE_URL and no Settings backend → 503 when transcribe_enabled."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_URL", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_TOKEN", raising=False)
    client = _client()
    r = client.post("/bots", headers=HEADERS,
                    json={"platform": "google_meet", "native_meeting_id": "no-stt"})
    assert r.status_code == 503
    assert "no transcription backend configured" in r.text


def test_post_bots_transcribe_with_settings_stt_passes(monkeypatch):
    """Settings-configured backend (monkeypatched _resolve_transcription_backend) → spawn proceeds."""
    from meeting_api.bot_spawn import service as spawn_service

    monkeypatch.setenv("ADMIN_TOKEN", SECRET)

    async def fake_resolve(user_id):
        return {"url": "https://stt-settings.example.com"}

    monkeypatch.setattr(spawn_service, "_resolve_transcription_backend", fake_resolve)

    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    r = client.post("/bots", headers=HEADERS,
                    json={"platform": "google_meet", "native_meeting_id": "settings-stt"})
    assert r.status_code == 201, r.text
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["transcriptionServiceUrl"] == "https://stt-settings.example.com"


# ── Settings → transcription backend: the configured STT (user pref > platform) beats the env ────

async def test_request_bot_configured_transcription_backend_overrides_env(monkeypatch):
    """A backend configured in Settings (resolved by admin-api's bot-context: user pref >
    platform setting) rides the invocation INSTEAD of the process env — including the token:
    the env token belongs to the ENV backend, never to a user-supplied endpoint."""
    from meeting_api.bot_spawn import service as spawn_service

    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt-env.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-env")

    async def fake_resolve(user_id):
        assert user_id == USER
        return {"url": "https://stt-mine.example.com"}

    monkeypatch.setattr(spawn_service, "_resolve_transcription_backend", fake_resolve)
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                      native_meeting_id="abc-defg-hij", redis_url="redis://redis:6379/0",
                      token_secret=SECRET)
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["transcriptionServiceUrl"] == "https://stt-mine.example.com"
    assert "transcriptionServiceToken" not in inv  # env token does NOT leak to the custom backend


async def test_request_bot_env_transcription_stays_without_settings(monkeypatch):
    """No configured backend (unset ADMIN_API_URL / nothing stored) → the pre-Settings env path,
    unchanged."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt-env.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-env")
    monkeypatch.delenv("ADMIN_API_URL", raising=False)

    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                      native_meeting_id="abc-defg-hij", redis_url="redis://redis:6379/0",
                      token_secret=SECRET)
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["transcriptionServiceUrl"] == "https://stt-env.vexa.ai"
    assert inv["transcriptionServiceToken"] == "tok-env"


# ── route: meeting_url passthrough is SSRF-validated at entry (jitsi/zoom, TAKE on #543) ─────────
#
# platform=jitsi (and zoom) carries an arbitrary caller URL straight to the bot's browser.
# The route now 422s non-https, IP-literal, and localhost URLs; a real hostname deployment
# is the negative control that proves the guard discriminates.

def test_post_bots_jitsi_http_url_422(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "jitsi", "native_meeting_id": "Room",
                             "meeting_url": "http://meet.example.org/Room"})
    assert r.status_code == 422, r.text
    assert "https" in r.json()["detail"]


def test_post_bots_jitsi_private_ip_url_422(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "jitsi", "native_meeting_id": "Room",
                             "meeting_url": "https://10.0.0.5/Room"})
    assert r.status_code == 422, r.text
    assert "IP literal" in r.json()["detail"]


def test_post_bots_jitsi_localhost_and_ipv6_422(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    client = _client()
    for bad in ("https://localhost/Room", "https://foo.localhost/Room", "https://[::1]/Room",
                "https://169.254.169.254/Room"):
        r = client.post("/bots", headers=HEADERS,
                        json={"platform": "jitsi", "native_meeting_id": "Room",
                              "meeting_url": bad})
        assert r.status_code == 422, f"{bad}: {r.status_code} {r.text}"


@pytest.mark.parametrize(
    "bad",
    (
        "https://2130706433/Room",
        "https://127.1/Room",
        "https://0177.0.0.1/Room",
        "https://0x7f000001/Room",
        "https://%31%32%37.0.0.1/Room",
    ),
)
def test_post_bots_rejects_browser_normalized_loopback_hosts(monkeypatch, bad):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)

    response = _client().post(
        "/bots",
        headers=HEADERS,
        json={"platform": "jitsi", "native_meeting_id": "Room", "meeting_url": bad},
    )

    assert response.status_code == 422, f"{bad}: {response.status_code} {response.text}"


def test_post_bots_jitsi_hostname_url_accepted(monkeypatch):
    """Negative control: a real https hostname deployment sails through the guard → 201."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "jitsi", "native_meeting_id": "Room",
                             "meeting_url": "https://meet.example.org/room"})
    assert r.status_code == 201, r.text


def test_post_bots_zoom_shares_meeting_url_guard(monkeypatch):
    """The zoom passthrough rides the SAME validator (one shared entry-point guard)."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "zoom", "native_meeting_id": "123456",
                             "meeting_url": "https://192.168.1.10/j/123456"})
    assert r.status_code == 422, r.text
