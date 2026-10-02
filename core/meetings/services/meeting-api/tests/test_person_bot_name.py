"""THE PERSON'S DEFAULT BOT NAME — one store, one lookup, and meetings resolves it.

There were three stores for one fact: `users.data.calendar_bot_name` (served on
`/internal/users/{id}/bot-context`, which `auto_join.py` reads), a per-calendar override that beats
it, and a `bot_name` key in a `.settings.json` file in the AGENT domain that only chat-dispatched
bots read. So the same person's bot showed up under one name when a calendar armed it and another
when they asked for it in chat, and nothing anywhere reconciled the two.

Founder ruling, 2026-09-02: NO FOURTH STORE. The bot-context value is the single source. Meetings
owns the bot, so meetings resolves the default — on the direct spawn path exactly as auto-join
already does on its own.

AND ONE LOOKUP (A21). The first shape of that fix injected a SECOND fetcher into `build_router`, so
`POST /bots` asked identity for the same `/internal/users/{id}/bot-context` body twice on one
request — once at 10s for the name, once inside `request_bot` at 5s for the transcription backend
and the capture-signal decision. Up to +15s on the path a person is waiting on, for one string,
while both fetchers' comments claimed to be the only one. The name now comes out of the context the
service already fetches: one hop, three readers.

PRECEDENCE, unchanged, and the same order auto-join uses:
    an explicit bot_name on the request  >  this person's default  >  the deployment's
"""
from __future__ import annotations

import inspect
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import build_router
from meeting_api.bot_spawn import service as spawn_service
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

USER = 7
HEADERS = {"x-user-id": str(USER)}
URL = "https://meet.google.com/abc-defg-hij"


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    """Minting a MeetingToken needs the deployment's secret — the same one every spawn test sets."""
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")


def _client(monkeypatch, context=None, calls=None):
    """The router with the ONE bot-context lookup stubbed — `service._fetch_bot_context`, the same
    seam `test_bot_spawn.py` uses for the transcription half of the same body."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()

    async def fetch(user_id: int):
        if calls is not None:
            calls.append(user_id)
        return context if context is not None else {}

    monkeypatch.setattr(spawn_service, "_fetch_bot_context", fetch)
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    return TestClient(app), runtime


def _spawned_name(runtime):
    """The name the bot actually goes into the room with, out of the recorded workload spec."""
    return json.dumps(runtime.specs[-1])


def _spawn(client, **body):
    return client.post("/bots", headers=HEADERS, json={
        "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
        "meeting_url": URL, **body})


def test_the_persons_default_is_used_when_the_caller_names_none(monkeypatch):
    monkeypatch.setenv("DEFAULT_BOT_NAME", "DeploymentBot")
    client, runtime = _client(monkeypatch, context={"bot_name": "Scribe"})
    r = _spawn(client)
    assert r.status_code == 201, r.text
    assert "Scribe" in _spawned_name(runtime)


def test_an_explicit_name_beats_the_persons_default(monkeypatch):
    client, runtime = _client(monkeypatch, context={"bot_name": "Scribe"})
    _spawn(client, bot_name="JustThisOnce")
    body = _spawned_name(runtime)
    assert "JustThisOnce" in body and "Scribe" not in body


def test_the_deployment_default_still_applies_when_the_person_has_none(monkeypatch):
    monkeypatch.setenv("DEFAULT_BOT_NAME", "DeploymentBot")
    client, runtime = _client(monkeypatch, context={})
    _spawn(client)
    assert "DeploymentBot" in _spawned_name(runtime)


def test_a_blank_name_from_identity_is_not_a_name(monkeypatch):
    monkeypatch.setenv("DEFAULT_BOT_NAME", "DeploymentBot")
    client, runtime = _client(monkeypatch, context={"bot_name": "   "})
    _spawn(client)
    assert "DeploymentBot" in _spawned_name(runtime)


def test_an_unreachable_identity_never_stops_a_bot_joining(monkeypatch):
    """A name is a nicety; joining the call is the product. Failing the spawn because a preference
    lookup timed out would trade the thing they asked for against the label on it.

    The REAL `_fetch_bot_context` against a dead address, not a stub: with one fetcher, the
    swallow-everything guarantee lives inside it and nowhere else, so that is what has to hold."""
    monkeypatch.setenv("DEFAULT_BOT_NAME", "DeploymentBot")
    monkeypatch.setenv("ADMIN_API_URL", "http://127.0.0.1:9")   # nothing listens
    monkeypatch.setenv("INTERNAL_API_SECRET", "unused-but-required-to-attempt-the-call")
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    r = TestClient(app).post("/bots", headers=HEADERS,
                             json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                                   "meeting_url": URL})
    assert r.status_code == 201, r.text
    assert "DeploymentBot" in _spawned_name(runtime)


# ── A21: exactly one bot-context fetch per spawn ───────────────────────────────────────────────
def test_identity_is_asked_exactly_once_per_spawn(monkeypatch):
    """The whole point. The same body answers three questions on this request — the transcription
    backend, the capture-signal decision, and the name — so it is fetched once."""
    calls = []
    client, _runtime = _client(monkeypatch, context={"bot_name": "Scribe"}, calls=calls)
    _spawn(client)
    assert calls == [USER]


def test_naming_the_bot_explicitly_does_not_add_or_remove_a_fetch(monkeypatch):
    """It used to REMOVE one (the router skipped its extra fetcher) — which is only a saving
    because there were two. There is one, `request_bot` needs it for the transcription backend
    whatever the bot is called, and the explicit name still wins without identity being consulted
    about it."""
    calls = []
    client, runtime = _client(monkeypatch, context={"bot_name": "Scribe"}, calls=calls)
    _spawn(client, bot_name="JustThisOnce")
    assert calls == [USER]
    assert "JustThisOnce" in _spawned_name(runtime)


def test_the_router_no_longer_carries_a_bot_context_fetcher():
    """Asserted on the signature, because the defect was structural: a second injected fetcher is
    invisible in behaviour (both return the same body) and shows up only as latency."""
    assert "fetch_bot_context" not in inspect.signature(build_router).parameters
    # …and nothing in the body calls one either (the docstring still explains why it is gone).
    body = inspect.getsource(build_router).split('"""', 2)[-1]
    assert "fetch_bot_context" not in body


def test_the_one_fetcher_keeps_its_own_timeout(monkeypatch):
    """`service._fetch_bot_context` is bounded at 5s. The removed router fetcher was bounded at 10s
    and ran in series with it — the worst case was the sum, not the max."""
    src = inspect.getsource(spawn_service._fetch_bot_context)
    assert "timeout=5.0" in src


def test_a_deployment_with_no_identity_edge_still_spawns(monkeypatch):
    """The offline/self-host shape: no ADMIN_API_URL, so `_fetch_bot_context` answers `{}` without
    reaching for anything."""
    monkeypatch.delenv("ADMIN_API_URL", raising=False)
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    monkeypatch.setenv("DEFAULT_BOT_NAME", "DeploymentBot")
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    r = TestClient(app).post("/bots", headers=HEADERS,
                             json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                                   "meeting_url": URL})
    assert r.status_code == 201, r.text
    assert "DeploymentBot" in _spawned_name(runtime)
