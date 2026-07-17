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


def test_invocation_carries_stt_model_when_provided():
    """#522: a validating OpenAI-compatible backend (Groq, vLLM) needs its served model id on
    every request. The deployment's choice rides the sealed invocation; absent → omitted, and
    the whisper client falls back to whisper-1 (today's wire)."""
    token = mint_meeting_token(1, USER, "google_meet", "abc-defg-hij", secret=SECRET)
    base = dict(meeting_id=1, platform="google_meet", meeting_url="https://meet.google.com/abc-defg-hij",
                bot_name="VexaBot", token=token, native_meeting_id="abc-defg-hij",
                connection_id="conn-1", redis_url="redis://redis:6379/0")
    inv = build_invocation(**base, transcription_model="whisper-large-v3-turbo")
    conforms_invocation(inv)
    assert inv["transcriptionModel"] == "whisper-large-v3-turbo"
    assert "transcriptionModel" not in build_invocation(**base)


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


def test_post_bots_forwards_automatic_leave_to_invocation(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    r = _client(repo, runtime).post(
        "/bots", headers=HEADERS,
        json={
            "platform": "google_meet", "native_meeting_id": "silence-window",
            "automatic_leave": {
                "max_wait_for_admission": 321_000,
                "max_time_left_alone": 12_345,
                "everyone_left_timeout": 99_999,
                "no_one_joined_timeout": 45_000,
            },
        },
    )
    assert r.status_code == 201, r.text
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["automaticLeave"] == {
        "waitingRoomTimeout": 321_000,
        "everyoneLeftTimeout": 12_345,
        "noOneJoinedTimeout": 45_000,
    }


def test_post_bots_legacy_everyone_left_alias_still_works(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    runtime = FakeRuntimeClient()
    r = _client(runtime=runtime).post(
        "/bots", headers=HEADERS,
        json={
            "platform": "google_meet", "native_meeting_id": "legacy-window",
            "automatic_leave": {"everyone_left_timeout": 23_456},
        },
    )
    assert r.status_code == 201, r.text
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["automaticLeave"]["everyoneLeftTimeout"] == 23_456


def test_post_bots_omits_everyone_left_when_not_explicit(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    runtime = FakeRuntimeClient()
    r = _client(runtime=runtime).post(
        "/bots", headers=HEADERS,
        json={"platform": "google_meet", "native_meeting_id": "module-default"},
    )
    assert r.status_code == 201, r.text
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["automaticLeave"] == {"waitingRoomTimeout": 600_000}


def test_post_bots_rejects_invalid_automatic_leave_timeout(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    r = _client().post(
        "/bots", headers=HEADERS,
        json={
            "platform": "google_meet", "native_meeting_id": "bad-window",
            "automatic_leave": {"max_time_left_alone": 0},
        },
    )
    assert r.status_code == 422
    assert "positive integer" in r.text


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

    monkeypatch.setenv("TRANSCRIPTION_MODEL", "env-model")

    monkeypatch.setattr(spawn_service, "_resolve_transcription_backend", fake_resolve)
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                      native_meeting_id="abc-defg-hij", redis_url="redis://redis:6379/0",
                      token_secret=SECRET)
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["transcriptionServiceUrl"] == "https://stt-mine.example.com"
    assert "transcriptionServiceToken" not in inv  # env token does NOT leak to the custom backend
    assert "transcriptionModel" not in inv  # env model names the ENV backend's model — same rule


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


async def test_request_bot_env_transcription_model_rides_invocation(monkeypatch):
    """#522 V1: ``TRANSCRIPTION_MODEL`` set on the deployment reaches every bot's invocation;
    unset → the field is omitted and the whisper client sends whisper-1 (today's wire)."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt-env.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-env")
    monkeypatch.delenv("ADMIN_API_URL", raising=False)

    monkeypatch.setenv("TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                      native_meeting_id="abc-defg-hij", redis_url="redis://redis:6379/0",
                      token_secret=SECRET)
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["transcriptionModel"] == "whisper-large-v3-turbo"

    monkeypatch.delenv("TRANSCRIPTION_MODEL", raising=False)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                      native_meeting_id="abc-defg-hij", redis_url="redis://redis:6379/0",
                      token_secret=SECRET)
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert "transcriptionModel" not in inv


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


# ── route: meeting_url-only bodies derive the addressing key, or refuse typed (#792) ─────────────
#
# api.v1's `meeting_url` description promises: "When provided without native_meeting_id, the URL is
# parsed to extract platform, native_meeting_id, and passcode automatically." A url-only body must
# therefore yield an ADDRESSABLE meeting (id derived via collector.meeting_link.parse_meeting_url)
# or a typed 422 — never a 201 persisting native_meeting_id='' (the unaddressable orphan).

def _spawn_env(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")


def test_post_bots_url_only_derives_native_id(monkeypatch):
    """Row 1: platform + meeting_url, no native id → 201 with the id derived from the URL."""
    _spawn_env(monkeypatch)
    repo = InMemoryMeetingRepo()
    r = _client(repo).post("/bots", headers=HEADERS,
                           json={"platform": "google_meet",
                                 "meeting_url": "https://meet.google.com/abc-defg-hij"})
    assert r.status_code == 201, r.text
    assert r.json()["native_meeting_id"] == "abc-defg-hij"
    row = repo._meetings[1]
    assert row["native_meeting_id"] == "abc-defg-hij"


def test_post_bots_url_only_meeting_is_stop_addressable(monkeypatch):
    """Row 2: a url-only spawn can be stopped via DELETE /bots/{platform}/{derived_id}."""
    from fastapi import FastAPI

    from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher, build_stop_router

    _spawn_env(monkeypatch)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    app.include_router(build_stop_router(repo, InMemoryCommandPublisher(), runtime))
    client = TestClient(app)
    assert client.post("/bots", headers=HEADERS,
                       json={"platform": "google_meet",
                             "meeting_url": "https://meet.google.com/abc-defg-hij"}).status_code == 201
    r = client.delete("/bots/google_meet/abc-defg-hij", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "stopping"


def test_post_bots_underivable_url_422_no_row(monkeypatch):
    """Row 3: https URL that passes the SSRF guard but yields no id → typed 422, nothing persisted."""
    _spawn_env(monkeypatch)
    repo = InMemoryMeetingRepo()
    r = _client(repo).post("/bots", headers=HEADERS,
                           json={"platform": "google_meet",
                                 "meeting_url": "https://example.com/not-a-meet-link"})
    assert r.status_code == 422, r.text
    assert "native_meeting_id" in r.json()["detail"]
    assert repo._meetings == {}  # never persist the '' orphan


def test_post_bots_url_only_no_platform_derives_both(monkeypatch):
    """Row 4: the report's literal body — meeting_url alone → platform AND id derived."""
    _spawn_env(monkeypatch)
    repo = InMemoryMeetingRepo()
    r = _client(repo).post("/bots", headers=HEADERS,
                           json={"meeting_url": "https://meet.google.com/abc-defg-hij"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["platform"] == "google_meet"
    assert body["native_meeting_id"] == "abc-defg-hij"


def test_post_bots_platform_url_mismatch_422(monkeypatch):
    """Row 5: supplied platform disagrees with the URL-derived one → 422 naming both."""
    _spawn_env(monkeypatch)
    repo = InMemoryMeetingRepo()
    r = _client(repo).post("/bots", headers=HEADERS,
                           json={"platform": "teams",
                                 "meeting_url": "https://meet.google.com/abc-defg-hij"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "teams" in detail and "google_meet" in detail
    assert repo._meetings == {}


def test_post_bots_url_only_jitsi_derives_room(monkeypatch):
    """F2: jitsi derivation accepted — the room (+host scope) becomes the native id, URL rides along."""
    _spawn_env(monkeypatch)
    repo = InMemoryMeetingRepo()
    r = _client(repo).post("/bots", headers=HEADERS,
                           json={"meeting_url": "https://meet.example.org/daily"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["platform"] == "jitsi"
    assert body["native_meeting_id"] == "daily@meet.example.org"


def test_post_bots_url_only_derives_passcode(monkeypatch):
    """F4: the contract sentence also promises passcode extraction — zoom ?pwd= rides into the
    invocation when the body carries none."""
    _spawn_env(monkeypatch)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    r = _client(repo, runtime).post("/bots", headers=HEADERS,
                                    json={"meeting_url": "https://us02web.zoom.us/j/1234567890?pwd=sEcReT123"})
    assert r.status_code == 201, r.text
    assert r.json()["native_meeting_id"] == "1234567890"
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["passcode"] == "sEcReT123"


def test_post_bots_explicit_native_id_unchanged_by_url(monkeypatch):
    """Row 6 companion: an explicit native_meeting_id is NEVER overridden by the URL (derivation
    only fills the gap; the valid 0.12 body is byte-identical)."""
    _spawn_env(monkeypatch)
    repo = InMemoryMeetingRepo()
    r = _client(repo).post("/bots", headers=HEADERS,
                           json={"platform": "google_meet", "native_meeting_id": "xyz-explicit-id",
                                 "meeting_url": "https://meet.google.com/abc-defg-hij"})
    assert r.status_code == 201, r.text
    assert repo._meetings[1]["native_meeting_id"] == "xyz-explicit-id"


# ── #816 hardening: a non-spawnable platform is refused typed, BEFORE any DB write ──────────────
# api.v1 seals MORE platforms than invocation.v1 (`browser_session` — a provisioning workload, not
# a meeting bot). With a meeting_url attached, such a request used to pass the constructibility
# guard, WRITE its `requested` row, then die inside build_invocation's sealed-schema validation:
# a 500 plus an orphaned active row that 409'd the user's retry on the dedup guard.


def test_browser_session_with_url_is_422_and_writes_no_row(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    repo = InMemoryMeetingRepo()
    r = _client(repo).post("/bots", headers=HEADERS, json={
        "platform": "browser_session",
        "native_meeting_id": "bs-deadbeef",
        "meeting_url": "https://internal.example/browser-session",
    })
    assert r.status_code == 422, f"{r.status_code} {r.text}"
    detail = r.json()["detail"]
    assert "browser_session" in detail and "816" in detail, (
        f"the refusal must name the tracked restoration, got: {detail}"
    )
    assert repo._meetings == {}, f"refused spawn wrote a meeting row: {repo._meetings}"

    # And the retry is NOT poisoned: an ordinary meeting on the same repo still spawns.
    ok = _client(repo).post("/bots", headers=HEADERS, json={
        "platform": "google_meet", "native_meeting_id": "after-refusal",
    })
    assert ok.status_code == 201, ok.text


def test_spawnable_platforms_is_the_sealed_invocation_enum():
    """SSOT: the router's refusal set is READ from the sealed invocation.v1 schema, so it can
    never drift from what build_invocation will actually accept."""
    from meeting_api.bot_spawn.invocation import SPAWNABLE_PLATFORMS, _INVOCATION_SCHEMA

    assert SPAWNABLE_PLATFORMS == frozenset(_INVOCATION_SCHEMA["$defs"]["Platform"]["enum"])
    assert "browser_session" not in SPAWNABLE_PLATFORMS
