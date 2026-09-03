"""THE PERSON'S DEFAULT BOT NAME — one store, and meetings resolves it.

There were three stores for one fact: `users.data.calendar_bot_name` (served on
`/internal/users/{id}/bot-context`, which `auto_join.py:324` reads), a per-calendar override that
beats it, and a `bot_name` key in a `.settings.json` file in the AGENT domain that only
chat-dispatched bots read. So the same person's bot showed up under one name when a calendar armed
it and another when they asked for it in chat, and nothing anywhere reconciled the two.

Founder ruling, 2026-09-02: NO FOURTH STORE. The bot-context value is the single source. Meetings
owns the bot, so meetings resolves the default — here, on the direct spawn path, exactly as
auto-join already does on its own. Callers stop resolving it: the rig stops reading a workspace
file, and flows stops passing a name at all.

PRECEDENCE, and it is the same order auto-join uses:
    an explicit bot_name on the request  >  this person's default  >  the deployment's
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import build_router
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

USER = 7
HEADERS = {"x-user-id": str(USER)}
URL = "https://meet.google.com/abc-defg-hij"


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    """Minting a MeetingToken needs the deployment's secret — the same one every spawn test sets."""
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")


def _client(context=None, calls=None):
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()

    async def fetch_bot_context(user_id: int):
        if calls is not None:
            calls.append(user_id)
        return context

    app = FastAPI()
    app.include_router(build_router(repo, runtime, fetch_bot_context=fetch_bot_context))
    return TestClient(app), runtime


def _spawned_name(runtime):
    """The name the bot actually goes into the room with, out of the recorded workload spec."""
    import json
    return json.dumps(runtime.specs[-1])


def test_the_persons_default_is_used_when_the_caller_names_none(monkeypatch):
    monkeypatch.setenv("DEFAULT_BOT_NAME", "DeploymentBot")
    client, runtime = _client(context={"bot_name": "Scribe"})
    r = client.post("/bots", headers=HEADERS,
                    json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                          "meeting_url": URL})
    assert r.status_code == 201, r.text
    assert "Scribe" in _spawned_name(runtime)


def test_an_explicit_name_beats_the_persons_default():
    client, runtime = _client(context={"bot_name": "Scribe"})
    client.post("/bots", headers=HEADERS,
                json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                      "meeting_url": URL, "bot_name": "JustThisOnce"})
    body = _spawned_name(runtime)
    assert "JustThisOnce" in body and "Scribe" not in body


def test_the_deployment_default_still_applies_when_the_person_has_none(monkeypatch):
    monkeypatch.setenv("DEFAULT_BOT_NAME", "DeploymentBot")
    client, runtime = _client(context={})
    client.post("/bots", headers=HEADERS,
                json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                      "meeting_url": URL})
    assert "DeploymentBot" in _spawned_name(runtime)


def test_an_unreachable_identity_never_stops_a_bot_joining(monkeypatch):
    """A name is a nicety; joining the call is the product. Failing the spawn because a preference
    lookup timed out would trade the thing they asked for against the label on it."""
    monkeypatch.setenv("DEFAULT_BOT_NAME", "DeploymentBot")
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()

    async def boom(user_id: int):
        raise RuntimeError("identity is down")

    app = FastAPI()
    app.include_router(build_router(repo, runtime, fetch_bot_context=boom))
    r = TestClient(app).post("/bots", headers=HEADERS,
                             json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                                   "meeting_url": URL})
    assert r.status_code == 201, r.text
    assert "DeploymentBot" in _spawned_name(runtime)


def test_identity_is_not_asked_when_the_caller_already_named_the_bot():
    """One fewer hop on the path a person is waiting on, and the answer could not change anything."""
    calls = []
    client, _runtime = _client(context={"bot_name": "Scribe"}, calls=calls)
    client.post("/bots", headers=HEADERS,
                json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                      "meeting_url": URL, "bot_name": "JustThisOnce"})
    assert calls == []


def test_a_deployment_with_no_identity_edge_still_spawns(monkeypatch):
    """`fetch_bot_context=None` is the offline/self-host shape — no admin edge configured."""
    monkeypatch.setenv("DEFAULT_BOT_NAME", "DeploymentBot")
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    r = TestClient(app).post("/bots", headers=HEADERS,
                             json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                                   "meeting_url": URL})
    assert r.status_code == 201, r.text
    assert "DeploymentBot" in _spawned_name(runtime)
