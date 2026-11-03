"""A chat session id is bounded where it enters agent-api.

The session becomes part of the chat's unit id, its stream topics, its continuity file and the
runtime's names for its worker. `ChatBody.session` (and `ResetBody.session`, which drops that
file) took any string; a session holding `/`, `..`, spaces or 300 characters reached all of them.
Every id a producer mints still passes; anything else is a 422 before a turn is dispatched.
"""
from __future__ import annotations

import secrets
import uuid

import pytest
from pydantic import ValidationError

from control_plane.bodies import ChatBody, ResetBody
from tests.test_dispatch_sink import KEY, sink  # noqa: F401 — the fixture
from control_plane import identity_token

PRODUCED = [
    "", "main", "onboarding",                           # the defaults (agent-api, flows' mailbox)
    "chat-lq3x9k2a",                                    # the terminal's new chat
    "meet-104", "meet-9f2c",                            # a meeting's chat (terminal, flows)
    "scaffold-" + secrets.token_urlsafe(32),            # the terminal's scaffold chat
    "scaffold--Ab_9",                                   # token_urlsafe may start with - or _
    str(uuid.uuid4()),                                  # a UUID
    "group-acme-team",                                  # flows' group onboarding
    "a" * 128,
]
REFUSED = ["../x", "a/b", "..", ".hidden", "-lead", "_lead", "a b", "a\x00b", "meet-1\n",
           "é", "a" * 129, "chat:1", "x;rm"]


@pytest.mark.parametrize("session", PRODUCED)
def test_every_session_a_producer_mints_is_accepted(session):
    assert ChatBody(prompt="hi", session=session).session == session
    assert ResetBody(session=session).session == session


@pytest.mark.parametrize("session", REFUSED)
def test_any_other_session_is_refused(session):
    with pytest.raises(ValidationError):
        ChatBody(prompt="hi", session=session)
    with pytest.raises(ValidationError):
        ResetBody(session=session)


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/submit", "/api/chat/reset"])
@pytest.mark.parametrize("session", ["../../escape", "a/b", "x" * 300])
def test_the_routes_answer_422_and_dispatch_nothing(sink, path, session):  # noqa: F811
    client, runtime = sink
    person = {identity_token.HEADER: identity_token.sign(KEY, {"sub": "7"})}
    r = client.post(path, json={"prompt": "hi", "session": session}, headers=person)
    assert r.status_code == 422, r.text
    assert runtime.spawned == []


# ── S70: every other door that takes a session — query, path, naming bodies, the rail order ──────

# Encoded where the value travels in a URL, so the route (not the client) sees the hostile text.
_QUERY_REFUSED = ["..%2F..%2Fescape", "a%2Fb", "x" * 300, "a%20b", ".hidden", "chat%3A1"]


def _person():
    return {identity_token.HEADER: identity_token.sign(KEY, {"sub": "7"})}


@pytest.mark.parametrize("session", _QUERY_REFUSED)
def test_the_pending_list_refuses_a_session_outside_the_bound(sink, session):  # noqa: F811
    client, _ = sink
    r = client.get(f"/api/chat/pending?session={session}", headers=_person())
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("session", ["x" * 300, ".hidden", "-lead", "chat%3A1", "a%20b", "%2E%2E"])
def test_the_history_path_refuses_a_session_outside_the_bound(sink, session):  # noqa: F811
    client, _ = sink
    r = client.get(f"/api/sessions/{session}/history", headers=_person())
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("path", ["/api/chat/name", "/api/chat/name/agent"])
@pytest.mark.parametrize("session", ["../../escape", "a/b", "x" * 129, "a b", "chat:1"])
def test_the_naming_bodies_refuse_a_session_outside_the_bound(sink, path, session):  # noqa: F811
    client, _ = sink
    r = client.post(path, json={"session": session, "title": "A title"}, headers=_person())
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("session", ["../../escape", "x" * 300, "a b"])
def test_the_rail_order_and_the_target_refuse_a_session_outside_the_bound(sink, session):  # noqa: F811
    client, _ = sink
    r = client.put("/api/chat/order", json={"order": ["main", session]}, headers=_person())
    assert r.status_code == 422, r.text
    r = client.post("/api/chat/target", json={"session": session, "workspace": ""}, headers=_person())
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("session", ["main", "chat-lq3x9k2a", "meet-104", str(uuid.uuid4())])
def test_a_produced_session_still_reaches_those_doors(sink, session):  # noqa: F811
    client, _ = sink
    assert client.get(f"/api/sessions/{session}/history", headers=_person()).status_code != 422
    assert client.put("/api/chat/order", json={"order": [session]}, headers=_person()).status_code != 422
