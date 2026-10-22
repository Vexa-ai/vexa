"""A bot's meeting_url: the host belongs to the platform and reaches the internet, not the deployment.

The bot's Chromium navigates wherever ``meeting_url`` points, and any authenticated caller chooses it.
These tests hold ``POST /bots`` and the calendar auto-join to the meeting host rule
(``bot_spawn.meeting_host``): the host is one of the platform's (or, for Jitsi and Zoom front doors,
an organisation's own), and every address it resolves to is public or named by the operator in
``VEXA_MEETING_HOST_ALLOW``. DNS is replaced by a table (``meeting_host.resolver``), offline.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import build_router, meeting_host, request_bot
from meeting_api.bot_spawn.auto_join import auto_join_tick
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.meeting_host import MeetingHostRefused

SECRET = "test-admin-token"
USER = 7
HEADERS = {"x-user-id": str(USER)}
PUBLIC = "93.184.215.14"

DNS = {
    "inside.zoom.us": ["10.0.0.7"],
    "meet.evil.example": ["169.254.169.254"],
    "jitsi.evil.example": ["100.64.0.9"],
    "mixed.jitsi.example": [PUBLIC, "192.168.1.10"],
    "v6.jitsi.example": ["fd00::5"],
    "loop.jitsi.example": ["127.0.0.1"],
    "jitsi.corp.example": ["10.20.1.5"],
    "meta.corp.example": ["169.254.169.254"],
    "other.corp.example": ["10.30.0.4"],
}


def _resolve(host: str):
    return DNS.get(host, [PUBLIC])


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.delenv("VEXA_MEETING_HOST_ALLOW", raising=False)
    monkeypatch.setattr(meeting_host, "resolver", _resolve)


def _client(repo=None, runtime=None):
    app = FastAPI()
    app.include_router(build_router(repo or InMemoryMeetingRepo(), runtime or FakeRuntimeClient()))
    return TestClient(app)


def _post(body, runtime=None):
    runtime = runtime or FakeRuntimeClient()
    r = _client(runtime=runtime).post("/bots", headers=HEADERS, json=body)
    return r, runtime


# ── literal addresses (already refused before this rule; kept as anchors) ───────────────────────

@pytest.mark.parametrize("url", ["http://169.254.169.254/", "https://169.254.169.254/latest/meta-data",
                                 "https://10.0.0.5/Room", "https://[fd00::1]/Room"])
def test_a_literal_internal_address_is_refused(url):
    r, runtime = _post({"platform": "jitsi", "native_meeting_id": "Room", "meeting_url": url})
    assert r.status_code == 422, r.text
    assert runtime.specs == []


# ── a dotted host that resolves inside ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("platform,url", [
    ("zoom", "https://inside.zoom.us/j/1234567890"),
    ("jitsi", "https://meet.evil.example/room"),
    ("jitsi", "https://jitsi.evil.example/room"),
    ("jitsi", "https://mixed.jitsi.example/room"),
    ("jitsi", "https://v6.jitsi.example/room"),
    ("jitsi", "https://loop.jitsi.example/room"),
])
def test_a_host_that_resolves_inside_is_refused(platform, url):
    native = "1234567890" if platform == "zoom" else "room"
    r, runtime = _post({"platform": platform, "native_meeting_id": native, "meeting_url": url})
    assert r.status_code == 422, r.text
    assert "internal or private" in r.json()["detail"]
    assert runtime.specs == []


def test_a_derived_jitsi_link_that_resolves_inside_is_refused():
    """The url-only body (platform derived from the link) runs the same rule."""
    r, runtime = _post({"meeting_url": "https://meet.evil.example/room"})
    assert r.status_code == 422, r.text
    assert runtime.specs == []


def test_an_unresolvable_host_is_refused(monkeypatch):
    monkeypatch.setattr(meeting_host, "resolver", lambda host: [])
    r, runtime = _post({"platform": "jitsi", "native_meeting_id": "room",
                        "meeting_url": "https://nowhere.jitsi.example/room"})
    assert r.status_code == 422, r.text
    assert "does not resolve" in r.json()["detail"]


# ── the host belongs to the platform ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("platform,native,url", [
    ("google_meet", "abc-defg-hij", "https://evil.example/abc-defg-hij"),
    ("google_meet", "abc-defg-hij", "https://meet.google.com.evil.example/abc-defg-hij"),
    ("teams", "19:meeting_x@thread.v2", "https://evil.example/l/meetup-join/19:meeting_x@thread.v2"),
    ("teams", "19:meeting_x@thread.v2", "https://teams.microsoft.com.evil.example/l/meetup-join/x"),
    ("zoom", "1234567890", "https://evil.example/somewhere"),
    ("zoom", "1234567890", "https://evil.example/j/1234567890"),
])
def test_a_host_off_the_platform_is_refused(platform, native, url):
    r, runtime = _post({"platform": platform, "native_meeting_id": native, "meeting_url": url})
    assert r.status_code == 422, r.text
    assert "is not a" in r.json()["detail"]
    assert runtime.specs == []


# ── positive controls: real meeting links on their platforms' hosts ──────────────────────────────

@pytest.mark.parametrize("body", [
    {"meeting_url": "https://meet.google.com/abc-defg-hij"},
    {"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
     "meeting_url": "https://meet.google.com/abc-defg-hij?authuser=0"},
    {"meeting_url": "https://teams.microsoft.com/l/meetup-join/19%3ameeting_NjE2@thread.v2/0?context=%7b%7d"},
    {"platform": "teams", "native_meeting_id": "19:meeting_NjE2@thread.v2",
     "meeting_url": "https://teams.microsoft.com/dl/launcher/launcher.html?url=%2F_%23%2Fl%2Fmeetup-join%2F19%3Ameeting_NjE2%40thread.v2&type=meetup-join"},
    {"meeting_url": "https://teams.live.com/meet/9387472658239?p=AbCdEf"},
    {"platform": "teams", "native_meeting_id": "397421056486982",
     "meeting_url": "https://gov.teams.microsoft.us/meet/397421056486982?p=x"},
    {"meeting_url": "https://us05web.zoom.us/j/81234567890?pwd=abc"},
    {"meeting_url": "https://acme.zoom.us/j/81234567890"},
    {"meeting_url": "https://zoom.us/j/81234567890"},
    {"meeting_url": "https://zoom-lfx.platform.linuxfoundation.org/meeting/91234567890?password=abc"},
    {"meeting_url": "https://meet.jit.si/VexaStandup"},
    {"meeting_url": "https://meet.example.org/VexaStandup"},
    {"platform": "jitsi", "native_meeting_id": "room", "meeting_url": "https://calls.example.io/room"},
])
def test_a_real_meeting_link_is_admitted(monkeypatch, body):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    r, runtime = _post(body)
    assert r.status_code == 201, r.text
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert "meetingHostAllow" not in inv


# ── the operator's allowance: an on-prem Jitsi ───────────────────────────────────────────────────

def test_a_jitsi_host_on_the_operator_list_is_admitted(monkeypatch):
    monkeypatch.setenv("VEXA_MEETING_HOST_ALLOW", "jitsi.corp.example")
    r, runtime = _post({"platform": "jitsi", "native_meeting_id": "room",
                        "meeting_url": "https://jitsi.corp.example/room"})
    assert r.status_code == 201, r.text
    inv = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert inv["meetingHostAllow"] == ["jitsi.corp.example"]


def test_a_network_on_the_operator_list_admits_its_hosts_and_literals(monkeypatch):
    monkeypatch.setenv("VEXA_MEETING_HOST_ALLOW", "10.20.0.0/16")
    r, _ = _post({"platform": "jitsi", "native_meeting_id": "room",
                  "meeting_url": "https://jitsi.corp.example/room"})
    assert r.status_code == 201, r.text
    r, _ = _post({"platform": "jitsi", "native_meeting_id": "room",
                  "meeting_url": "https://10.20.1.5/room"})
    assert r.status_code == 201, r.text
    r, _ = _post({"platform": "jitsi", "native_meeting_id": "room",
                  "meeting_url": "https://other.corp.example/room"})
    assert r.status_code == 422, r.text


def test_the_list_admits_another_internal_host_nothing(monkeypatch):
    monkeypatch.setenv("VEXA_MEETING_HOST_ALLOW", "jitsi.corp.example")
    r, _ = _post({"platform": "jitsi", "native_meeting_id": "room",
                  "meeting_url": "https://other.corp.example/room"})
    assert r.status_code == 422, r.text


def test_the_list_never_admits_metadata(monkeypatch):
    monkeypatch.setenv("VEXA_MEETING_HOST_ALLOW", "meta.corp.example")
    r, _ = _post({"platform": "jitsi", "native_meeting_id": "room",
                  "meeting_url": "https://meta.corp.example/room"})
    assert r.status_code == 422, r.text


def test_the_list_never_moves_meet_or_teams_off_their_hosts(monkeypatch):
    monkeypatch.setenv("VEXA_MEETING_HOST_ALLOW", "jitsi.corp.example")
    r, _ = _post({"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                  "meeting_url": "https://jitsi.corp.example/abc-defg-hij"})
    assert r.status_code == 422, r.text


def test_a_zoom_front_door_on_the_operator_list_is_admitted(monkeypatch):
    monkeypatch.setenv("VEXA_MEETING_HOST_ALLOW", "jitsi.corp.example")
    r, _ = _post({"platform": "zoom", "native_meeting_id": "1234567890",
                  "meeting_url": "https://jitsi.corp.example/whatever"})
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("raw", ["*.corp.example", "169.254.0.0/16", "127.0.0.1", "localhost",
                                 "https://jitsi.corp.example", "0.0.0.0/0"])
def test_a_malformed_list_refuses_the_boot(raw):
    from meeting_api.__main__ import _meeting_host_allow_at_boot
    from meeting_api.config_preflight import ConfigError

    with pytest.raises(ConfigError, match="VEXA_MEETING_HOST_ALLOW"):
        _meeting_host_allow_at_boot({"VEXA_MEETING_HOST_ALLOW": raw})


# ── every spawn path: request_bot and the calendar auto-join ─────────────────────────────────────

async def test_request_bot_holds_the_rule_before_any_write():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    with pytest.raises(MeetingHostRefused):
        await request_bot(repo, runtime, user_id=USER, platform="jitsi", native_meeting_id="room",
                          meeting_url="https://meet.evil.example/room",
                          redis_url="redis://redis:6379/0", token_secret=SECRET)
    assert repo._meetings == {}
    assert runtime.specs == []


async def test_the_auto_join_stamps_a_refused_link_and_spawns_nothing():
    now = datetime(2026, 7, 10, 15, 0, 0, tzinfo=timezone.utc)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    repo._meetings[1] = {
        "id": 1, "user_id": USER, "platform": "jitsi", "native_meeting_id": "room@meet.evil.example",
        "platform_specific_id": "room@meet.evil.example", "status": "scheduled",
        "bot_container_id": None, "start_time": None, "end_time": None,
        "data": {"title": "t", "auto_join": True, "scheduled_at": (now + timedelta(seconds=30)).isoformat(),
                 "constructed_meeting_url": "https://meet.evil.example/room"},
        "created_at": "2026-07-08T09:00:00Z", "updated_at": "2026-07-08T09:00:00Z",
    }
    counters = await auto_join_tick(repo, runtime, transcribe_gate=lambda: None, now=now,
                                    token_secret="s", redis_url="redis://r", allow_uncapped=True)
    assert counters["errors"] == 1 and counters["spawned"] == 0
    assert "internal or private" in repo._meetings[1]["data"]["auto_join_error"]
    assert runtime.specs == []
