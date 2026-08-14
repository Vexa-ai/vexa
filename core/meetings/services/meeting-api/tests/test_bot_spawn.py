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
    DuplicateMeeting,
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


# ── O-TEL-1 fixture collection: captureSignalEnabled end-to-end through the spawn ────────────────

_INV_BASE = dict(meeting_id=1, platform="google_meet",
                 meeting_url="https://meet.google.com/abc-defg-hij", bot_name="VexaBot",
                 native_meeting_id="abc-defg-hij", connection_id="conn-1",
                 redis_url="redis://redis:6379/0")


def test_invocation_carries_capture_signal_enabled_and_strips_only_none():
    """The kill switch travels as an explicit ``false``, not as an omission.

    ``build_invocation`` strips ``None`` — so an omitted flag would leave the bot on its own
    ``VEXA_CAPTURE_SIGNAL`` env default, which is exactly the wrong outcome for a spawn whose
    operator has just turned collection OFF. ``False`` is not ``None``, so it survives the strip;
    this pins that, because the day it stops being true nothing else would notice.
    """
    token = mint_meeting_token(1, USER, "google_meet", "abc-defg-hij", secret=SECRET)
    base = dict(_INV_BASE, token=token)

    on = build_invocation(**base, capture_signal_enabled=True)
    conforms_invocation(on)
    assert on["captureSignalEnabled"] is True

    off = build_invocation(**base, capture_signal_enabled=False)
    conforms_invocation(off)
    assert off["captureSignalEnabled"] is False, "the kill switch must ride as false, not vanish"

    # Unset (the desktop / local composition root) → omitted, so the bot keeps its env default.
    assert "captureSignalEnabled" not in build_invocation(**base)


def test_invocation_tape_is_independent_of_recording_enabled():
    """A meeting nobody asked to record still yields a fixture — the two flags are orthogonal, and
    the sealed contract says so ("the transcript/recording paths are unaffected either way")."""
    token = mint_meeting_token(1, USER, "google_meet", "abc-defg-hij", secret=SECRET)
    inv = build_invocation(**dict(_INV_BASE, token=token),
                           recording_enabled=False, capture_signal_enabled=True,
                           recording_upload_url="http://meeting-api:8080/internal/recordings/upload")
    conforms_invocation(inv)
    assert inv["recordingEnabled"] is False and inv["captureSignalEnabled"] is True
    # The tape rides the SAME upload endpoint the recording chunks use, so a non-recording spawn
    # still needs the URL — without it the bot has a tape and nowhere to put it.
    assert inv["recordingUploadUrl"].endswith("/internal/recordings/upload")


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


# ── regression guard: profile resolution must not regress (#875 follow-up) ───────────────────────
#
# build_workload_spec used to hardcode profile="meeting-bot" for every platform. A discord spawn
# then ran the Playwright browser image instead of the discord-bot DAVE service — the browser bot
# maps any platform it doesn't recognize to google_meet and navigates a blank URL, so the lane could
# never work end to end. profile_for_platform (invocation.py) is the ONE place platform → profile is
# decided; these tests fail if that mapping regresses in either direction.

@pytest.mark.parametrize("platform,meeting_url", [
    ("google_meet", "https://meet.google.com/abc-defg-hij"),
    ("teams", "https://teams.microsoft.com/l/meetup-join/x"),
    ("zoom", "https://zoom.us/j/123456789"),
    ("jitsi", "https://meet.jit.si/Room"),
])
def test_workload_spec_non_discord_platforms_resolve_meeting_bot(platform, meeting_url):
    inv = build_invocation(
        meeting_id=1, platform=platform, meeting_url=meeting_url,
        bot_name="VexaBot", token="t", native_meeting_id="x", connection_id="conn-1",
        redis_url="redis://redis:6379/0",
    )
    spec = build_workload_spec(workload_id="mtg-1-conn", invocation=inv)
    conforms_workload_spec(spec)
    assert spec["profile"] == "meeting-bot"


def test_workload_spec_discord_resolves_discord_bot_profile():
    """The BLOCKER fix: a discord spawn must resolve the discord-bot runtime profile (the DAVE
    voice-receive service), never meeting-bot (the Playwright browser image)."""
    inv = build_invocation(
        meeting_id=1, platform="discord", meeting_url=None,
        bot_name="VexaBot", token="t", native_meeting_id="222222222222222222",
        connection_id="conn-1", redis_url="redis://redis:6379/0",
    )
    spec = build_workload_spec(workload_id="mtg-1-conn", invocation=inv)
    conforms_workload_spec(spec)
    assert spec["profile"] == "discord-bot"


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


def test_iso_utc_marks_naive_utc_with_z():
    # Meeting time columns are naive but hold UTC. Serializing must emit a Z marker so a browser
    # parses it as UTC and renders local — a bare isoformat is read as LOCAL (the 6h-skew bug).
    from datetime import datetime, timezone

    from meeting_api.bot_spawn.adapters import _iso_utc

    assert _iso_utc(datetime(2026, 7, 20, 1, 0, 0)) == "2026-07-20T01:00:00Z"
    assert _iso_utc(datetime(2026, 7, 20, 1, 0, 0, tzinfo=timezone.utc)) == "2026-07-20T01:00:00Z"
    assert _iso_utc(None) is None


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


# ── #718: a workload DEAD AT START is refused, the row is failed with the reason, no `requested` lingers
async def test_request_bot_dead_on_arrival_fails_the_row(monkeypatch):
    """C2: the kernel answers 201 but with a workload that never started (state=stopped/start_failed).
    ``create_workload`` catches the dead body → ``SpawnFailed``; ``request_bot`` marks the meeting
    row ``failed`` with the reason so NO ``requested`` row remains, and creates no session.

    Negative control (the bug): before the fix the dead 201 sailed through, the row stayed
    ``requested``, and the reaper flipped it reason-less 5 minutes later."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient(dead_on_arrival=True)
    with pytest.raises(SpawnFailed) as ei:
        await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                          native_meeting_id="dead", redis_url="r", token_secret=SECRET)
    assert "start_failed" in str(ei.value)
    # exactly one row, and it is FAILED with the reason — not a lingering `requested`.
    rows = list(repo._meetings.values())
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "failed"
    assert row["data"]["completion_reason"] == "start_failed"
    assert "start_failed" in row["data"]["failure_reason"]
    assert repo.sessions == [], "no MeetingSession for a workload that never started"


async def test_request_bot_spawnfailed_fails_the_row(monkeypatch):
    """The same row-failing discipline on the runtime-error path (create_workload raises SpawnFailed,
    e.g. a non-201 from the kernel): the row is failed, not left ``requested``."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient(fail=True)
    with pytest.raises(SpawnFailed):
        await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                          native_meeting_id="boom", redis_url="r", token_secret=SECRET)
    assert list(repo._meetings.values())[0]["status"] == "failed"


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
    assert inv["automaticLeave"] == {"waitingRoomTimeout": 900_000}


def test_post_bots_lobby_budget_default_is_fifteen_minutes(monkeypatch):
    """#1208 — the deployment default the spawn ISSUES, with no caller opinion: 900s."""
    monkeypatch.delenv("VEXA_LOBBY_BUDGET_S", raising=False)
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    runtime = FakeRuntimeClient()
    r = _client(runtime=runtime).post(
        "/bots", headers=HEADERS,
        json={"platform": "google_meet", "native_meeting_id": "lobby-default"},
    )
    assert r.status_code == 201, r.text
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["automaticLeave"]["waitingRoomTimeout"] == 900_000


def test_post_bots_lobby_budget_honours_the_env_override(monkeypatch):
    """``VEXA_LOBBY_BUDGET_S`` configures the issued deadline — read per request, not frozen at
    import, so a deploy value takes effect without a code change."""
    monkeypatch.setenv("VEXA_LOBBY_BUDGET_S", "1200")
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    runtime = FakeRuntimeClient()
    r = _client(runtime=runtime).post(
        "/bots", headers=HEADERS,
        json={"platform": "google_meet", "native_meeting_id": "lobby-env"},
    )
    assert r.status_code == 201, r.text
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["automaticLeave"]["waitingRoomTimeout"] == 1_200_000


def test_lobby_budget_ignores_unusable_env_values(monkeypatch):
    """A blank, unparseable or non-positive override falls back to the default rather than issuing a
    zero-second deadline — a bot given a zero budget gives up before it has knocked."""
    from meeting_api.bot_spawn.service import lobby_budget_ms

    for bad in ("", "   ", "not-a-number", "0", "-5"):
        monkeypatch.setenv("VEXA_LOBBY_BUDGET_S", bad)
        assert lobby_budget_ms() == 900_000, bad


def test_explicit_max_wait_for_admission_still_beats_the_env_default(monkeypatch):
    """The caller's own opinion wins over the deployment default — the env only fills the gap."""
    monkeypatch.setenv("VEXA_LOBBY_BUDGET_S", "900")
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    runtime = FakeRuntimeClient()
    r = _client(runtime=runtime).post(
        "/bots", headers=HEADERS,
        json={
            "platform": "google_meet", "native_meeting_id": "lobby-explicit",
            "automatic_leave": {"max_wait_for_admission": 45_000},
        },
    )
    assert r.status_code == 201, r.text
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["automaticLeave"]["waitingRoomTimeout"] == 45_000


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


def test_post_bots_502_when_workload_dead_on_arrival(monkeypatch):
    """Route level (#718 A1): a workload dead at start → POST /bots is 502 naming the reason, and the
    meeting row is ``failed`` (NOT a lingering ``requested`` that would 409 the user's retry)."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient(dead_on_arrival=True)
    client = _client(repo, runtime)
    r = client.post("/bots", headers=HEADERS,
                    json={"platform": "google_meet", "native_meeting_id": "dead-201"})
    assert r.status_code == 502, f"a dead-at-start spawn must not 201; got {r.status_code}"
    assert "start_failed" in r.json()["detail"]
    rows = list(repo._meetings.values())
    assert len(rows) == 1 and rows[0]["status"] == "failed"


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
    """Settings-configured backend (monkeypatched bot-context) → spawn proceeds."""
    from meeting_api.bot_spawn import service as spawn_service

    monkeypatch.setenv("ADMIN_TOKEN", SECRET)

    async def fake_resolve(user_id):
        return {"transcription": {"url": "https://stt-settings.example.com",
                                  "provider": "customer"}}

    monkeypatch.setattr(spawn_service, "_fetch_bot_context", fake_resolve)

    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    r = client.post("/bots", headers=HEADERS,
                    json={"platform": "google_meet", "native_meeting_id": "settings-stt"})
    assert r.status_code == 201, r.text
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["transcriptionServiceUrl"] == "https://stt-settings.example.com"
    row = next(iter(repo._meetings.values()))
    assert row["data"]["transcription_provider"] == "customer"


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
        return {"transcription": {"url": "https://stt-mine.example.com",
                                  "provider": "customer"}}

    monkeypatch.setenv("TRANSCRIPTION_MODEL", "env-model")

    monkeypatch.setattr(spawn_service, "_fetch_bot_context", fake_resolve)
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                      native_meeting_id="abc-defg-hij", redis_url="redis://redis:6379/0",
                      token_secret=SECRET)
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["transcriptionServiceUrl"] == "https://stt-mine.example.com"
    assert "transcriptionServiceToken" not in inv  # env token does NOT leak to the custom backend
    assert "transcriptionModel" not in inv  # env model names the ENV backend's model — same rule
    row = next(iter(repo._meetings.values()))
    assert row["data"]["transcription_provider"] == "customer"


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
    row = next(iter(repo._meetings.values()))
    assert row["data"]["transcription_provider"] == "vexa"


async def test_request_bot_disabled_transcription_freezes_none_provider(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_URL", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_API_URL", raising=False)

    repo = InMemoryMeetingRepo()
    await request_bot(
        repo,
        FakeRuntimeClient(),
        user_id=USER,
        platform="google_meet",
        native_meeting_id="disabled-tx",
        transcribe_enabled=False,
        redis_url="redis://redis:6379/0",
        token_secret=SECRET,
    )

    row = next(iter(repo._meetings.values()))
    assert row["data"]["transcription_provider"] == "none"


async def test_request_bot_disabled_transcription_ignores_configured_provider(monkeypatch):
    from meeting_api.bot_spawn import service as spawn_service

    async def fake_resolve(_user_id):
        return {"transcription": {"url": "https://stt-mine.example.com",
                                  "provider": "customer"}}

    monkeypatch.setattr(spawn_service, "_fetch_bot_context", fake_resolve)
    repo = InMemoryMeetingRepo()
    await request_bot(
        repo,
        FakeRuntimeClient(),
        user_id=USER,
        platform="google_meet",
        native_meeting_id="disabled-configured-tx",
        transcribe_enabled=False,
        redis_url="redis://redis:6379/0",
        token_secret=SECRET,
    )

    row = next(iter(repo._meetings.values()))
    assert row["data"]["transcription_provider"] == "none"


async def test_request_bot_does_not_guess_provider_for_legacy_settings_response(monkeypatch):
    """A mixed-version identity response may have a URL but no provenance.

    The spawn may proceed for compatibility, but billing provenance must remain unresolved:
    inferring from the URL would turn an unknown customer endpoint into a Vexa charge.
    """
    from meeting_api.bot_spawn import service as spawn_service

    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt-env.vexa.ai")

    async def fake_resolve(user_id):
        return {"transcription": {"url": "https://legacy-settings.example.com"}}

    monkeypatch.setattr(spawn_service, "_fetch_bot_context", fake_resolve)
    repo = InMemoryMeetingRepo()
    await request_bot(
        repo,
        FakeRuntimeClient(),
        user_id=USER,
        platform="google_meet",
        native_meeting_id="legacy-provider",
        redis_url="redis://redis:6379/0",
        token_secret=SECRET,
    )

    row = next(iter(repo._meetings.values()))
    assert "transcription_provider" not in row["data"]


async def test_continue_meeting_refreezes_provider_for_the_new_session(monkeypatch):
    from meeting_api.bot_spawn import service as spawn_service

    selected = {"provider": "customer"}

    async def fake_resolve(_user_id):
        return {"transcription": {
            "url": "https://configured.example.com",
            "provider": selected["provider"],
        }}

    monkeypatch.setattr(spawn_service, "_fetch_bot_context", fake_resolve)
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    first = await request_bot(
        repo,
        runtime,
        user_id=USER,
        platform="google_meet",
        native_meeting_id="continued-provider",
        redis_url="redis://redis:6379/0",
        token_secret=SECRET,
    )
    assert repo._meetings[first["id"]]["data"]["transcription_provider"] == "customer"

    repo.set_status(first["id"], "completed")
    selected["provider"] = "vexa"
    await request_bot(
        repo,
        runtime,
        user_id=USER,
        platform="google_meet",
        native_meeting_id="continued-provider",
        continue_meeting=True,
        redis_url="redis://redis:6379/0",
        token_secret=SECRET,
    )

    assert repo._meetings[first["id"]]["data"]["transcription_provider"] == "vexa"


async def test_continue_meeting_never_reopens_an_artifact_deletion_tombstone(monkeypatch):
    """A deletion-pending/completed terminal row is immutable lifecycle evidence, not a row the
    continue path may reopen while storage erasure is in progress or after it completes."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt-env.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    first = await request_bot(
        repo, runtime, user_id=USER, platform="google_meet",
        native_meeting_id="deleted-artifact-row", redis_url="redis://redis:6379/0",
        token_secret=SECRET,
    )
    repo.set_status(first["id"], "completed")
    repo._meetings[first["id"]]["data"]["artifact_deletion"] = {"state": "pending"}
    with pytest.raises(DuplicateMeeting):
        await repo.reopen_meeting(meeting_id=first["id"])

    continued = await request_bot(
        repo, runtime, user_id=USER, platform="google_meet",
        native_meeting_id="deleted-artifact-row", continue_meeting=True,
        redis_url="redis://redis:6379/0", token_secret=SECRET,
    )

    assert continued["id"] != first["id"]
    assert repo._meetings[first["id"]]["status"] == "completed"
    assert first["id"] not in repo.reopened


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


def test_post_bots_discord_hostname_url_accepted(monkeypatch):
    """discord (#875) has no URL template (no _URL_TEMPLATES entry) and no meeting_url
    requirement (bot_spawn.NO_MEETING_URL_PLATFORMS) — but an OPTIONAL caller-supplied meeting_url
    still rides the SAME SSRF validator zoom/jitsi use, identically."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "discord", "native_meeting_id": "222222222222222222",
                             "meeting_url": "https://discord.com/channels/111111111111111111/222222222222222222"})
    assert r.status_code == 201, r.text
    assert r.json()["platform"] == "discord"


def test_post_bots_discord_spawns_discord_bot_profile(monkeypatch):
    """The BLOCKER fix end to end (#875): POST /bots with platform=discord must spawn the
    discord-bot runtime profile. Before the fix build_workload_spec hardcoded "meeting-bot" for
    every platform, so a discord request booted the Playwright browser image instead of the DAVE
    voice-receive service — the bot could never join a Discord voice channel."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    runtime = FakeRuntimeClient()
    r = _client(runtime=runtime).post("/bots", headers=HEADERS,
                       json={"platform": "discord", "native_meeting_id": "222222222222222222"})
    assert r.status_code == 201, r.text
    assert runtime.specs[-1]["profile"] == "discord-bot"


def test_post_bots_discord_meeting_is_stop_addressable(monkeypatch):
    """A discord spawn can be stopped via DELETE /bots/discord/{native_id} (stop_router's
    _SUPPORTED_PLATFORMS now carries 'discord')."""
    from fastapi import FastAPI

    from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher, build_stop_router

    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    app.include_router(build_stop_router(repo, InMemoryCommandPublisher(), runtime))
    client = TestClient(app)
    assert client.post("/bots", headers=HEADERS,
                       json={"platform": "discord", "native_meeting_id": "222222222222222222",
                             "meeting_url": "https://discord.com/channels/1/222222222222222222"}
                       ).status_code == 201
    r = client.delete("/bots/discord/222222222222222222", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "stopping"


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


def test_native_meeting_id_over_column_length_is_422_not_500(monkeypatch):
    """#843: `platform_specific_id` is varchar(255). An over-long id used to sail past this
    boundary and die at the INSERT on asyncpg's StringDataRightTruncationError — a 500 ~5.6s in,
    observed in production. It must be refused HERE, typed, and write no row."""
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    repo = InMemoryMeetingRepo()
    r = _client(repo).post("/bots", headers=HEADERS, json={
        "platform": "google_meet", "native_meeting_id": "A" * 20000,
    })
    assert r.status_code == 422, f"expected typed refusal, got {r.status_code} {r.text}"
    detail = r.json()["detail"]
    assert "255" in detail, f"the refusal must name the limit, got: {detail}"
    assert repo._meetings == {}, f"refused spawn wrote a meeting row: {repo._meetings}"


def test_native_meeting_id_with_nul_byte_is_422_not_500(monkeypatch):
    """#843: a NUL byte reaches Postgres as an invalid text value and 500s at the INSERT."""
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    repo = InMemoryMeetingRepo()
    r = _client(repo).post("/bots", headers=HEADERS, json={
        "platform": "google_meet", "native_meeting_id": "abc" + chr(0) + "def",
    })
    assert r.status_code == 422, f"expected typed refusal, got {r.status_code} {r.text}"
    assert "control" in r.json()["detail"].lower(), r.text
    assert repo._meetings == {}, f"refused spawn wrote a meeting row: {repo._meetings}"


def test_native_meeting_id_bounds_do_not_validate_SHAPE(monkeypatch):
    """NEGATIVE CONTROL — the guard bounds length/bytes and URL-structural chars ONLY, never the
    id's SEMANTIC shape.

    Production evidence: a bare-numeric Teams id (the dial-in kind) transcribed a real meeting
    (24368, 67 segments) while another of the SAME shape failed. Shape does not predict success,
    so a format rule would refuse working meetings. The Teams thread-id form
    (`19:…@thread.v2` — `: @ . _ -`) and Meet dash-codes must all still spawn; only URL-structural
    chars are refused (see #892 test below)."""
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    for odd_but_legal in (
        "474226440982", "abc-defg-hij", "x", "A" * 255,
        "19:meeting_AbC-dEf_123@thread.v2",  # Teams thread id: `:@._-` must survive the #892 guard
    ):
        repo = InMemoryMeetingRepo()
        r = _client(repo).post("/bots", headers=HEADERS, json={
            "platform": "google_meet", "native_meeting_id": odd_but_legal,
        })
        assert r.status_code == 201, f"{odd_but_legal!r} was refused: {r.status_code} {r.text}"


def test_native_meeting_id_with_url_chars_is_422_not_join_failure(monkeypatch):
    """#892: a `native_meeting_id` carrying URL-structural chars (a Teams passcode left on the id,
    `397421056486982?p=X8hc…`) is short and control-free, so it passed the #843/#855 length+control
    guards, then string-interpolated into `construct_meeting_url` to build a broken join URL
    (`…/l/meetup-join/…982?p=X8hc…` → join_failure) and stored an unfindable `platform_specific_id`.
    It must be refused HERE, typed 422, naming the fix, and write NO row."""
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    # The reproduced value from the issue, plus one per URL-structural class + a literal space.
    for bad_id in (
        "397421056486982?p=X8hcQVTnGNpGelJLSv",  # the reproduced Teams-passcode case
        "abc?def", "abc&def", "abc=def", "abc/def", "abc#def", "abc def",
    ):
        repo = InMemoryMeetingRepo()
        r = _client(repo).post("/bots", headers=HEADERS, json={
            "platform": "teams", "native_meeting_id": bad_id,
        })
        assert r.status_code == 422, f"{bad_id!r} expected typed 422, got {r.status_code} {r.text}"
        detail = r.json()["detail"]
        assert "native_meeting_id" in detail and "passcode" in detail, (
            f"the refusal must name the id and the fix, got: {detail}"
        )
        assert repo._meetings == {}, f"refused spawn wrote a meeting row: {repo._meetings}"

    # POSITIVE CONTROL — the bare id + a separate passcode still spawns (the meeting-13564 pattern:
    # pass the passcode in its own field, not glued onto the id).
    repo = InMemoryMeetingRepo()
    ok = _client(repo).post("/bots", headers=HEADERS, json={
        "platform": "teams", "native_meeting_id": "397421056486982",
        "passcode": "X8hcQVTnGNpGelJLSv",
    })
    assert ok.status_code == 201, f"bare id + separate passcode was refused: {ok.text}"


# ── O-TEL-1: what the spawn resolves when identity answers, and when it does not ─────────────────

async def test_spawn_defaults_capture_signal_on_when_identity_is_unreachable(monkeypatch):
    """No ADMIN_API_URL / a 500 / an admin-api that predates the field → the tape still runs.

    The failure this forbids is silent: a transient identity blip turns fixture collection off
    fleet-wide, prod looks entirely healthy, and nobody notices until someone asks why no fixtures
    arrived. The bot-context lookup is best-effort by contract, so its failure mode must be the
    product default, not the absence of one.
    """
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                      native_meeting_id="ctx-unreachable", redis_url="redis://redis:6379/0",
                      token_secret=SECRET)
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["captureSignalEnabled"] is True


@pytest.mark.parametrize("ctx,expected,slug", [
    ({"capture_signal": True}, True, "on"),
    ({"capture_signal": False}, False, "off"),
    ({}, True, "absent"),                     # an older admin-api has no such key → default ON
    ({"capture_signal": "false"}, True, "str"),   # only a real boolean false is the kill switch
])
async def test_spawn_threads_capture_signal_from_bot_context(monkeypatch, ctx, expected, slug):
    """One hop, two readers: the same best-effort bot-context call feeds the STT backend AND the
    tape flag. The string case is deliberate — identity normalizes the settings string into a real
    boolean, so a string arriving here means a contract drift, and defaulting ON is the safe read."""
    from meeting_api.bot_spawn import service as spawn_service

    monkeypatch.setenv("ADMIN_TOKEN", SECRET)

    async def fake_ctx(_user_id):
        return ctx

    monkeypatch.setattr(spawn_service, "_fetch_bot_context", fake_ctx)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                      native_meeting_id=f"ctx-{slug}", redis_url="redis://redis:6379/0",
                      token_secret=SECRET)
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["captureSignalEnabled"] is expected
