"""Deny tests — every act a card suggests that reaches other people happens only on the person's
recorded press (security gate 0.13.4, M1 scope).

The worker is told "creating, inviting and sending happen only after their press"
(`worker/engine.py` `inline_card_preamble`). Until now only adding a person to a chat was held to it
on the server; every other such act rested on the model. Each act below, called by the person's
WATCHING agent (a delegated identity in the human regime), is refused (403 `press_required`) unless
it carries the `#<pending>` of the person's own pressed answer to a card naming whom it reaches; the
press is spent, so one press is one act. The person's own session needs no press (the control).

Acts: a notice to other people (`notify` with `to`), a message into another person's conversation
(`message_agent`), a calendar event with attendees or one that emails them (`calendar_event_*`),
and publishing a workspace (`workspace_publish`).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from control_plane import git_credentials
from control_plane.identity_token import DELEGATION_HEADERS
from control_plane.routers import connections
from control_plane.routers import workspaces as ws_router
from tests.card_press import card, offer, press
from tests.test_access_agent_messages import _both_in, _conversation, _send, world as msg_world  # noqa: F401
from tests.test_notifications_routes import ALICE, JOE, _h, env  # noqa: F401


def _watching(who: str, unit: str) -> dict:
    """``who``'s chat worker, dispatched with them in the loop."""
    return {"X-User-Id": who, DELEGATION_HEADERS["regime"]: "human", DELEGATION_HEADERS["workspaces"]: "*",
            DELEGATION_HEADERS["unit"]: unit}


def _refused(r):
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["refused"] == "press_required", r.text


# ── notify: a notice to other people ─────────────────────────────────────────────────────────────

def _notify(client, headers, sid, **extra):
    return client.post("/api/notifications/notify", headers=headers,
                       json={"text": "Draft is ready", "session": sid, "to": [JOE], **extra})


def test_an_agent_notifies_other_people_only_on_the_persons_press(env):
    client, store, _bus, _root = env
    sid = store.create(ALICE, name="team", title="Board prep")["id"]
    store.add_participant(sid, ALICE, JOE)
    agent = {**_h(ALICE), **_watching(ALICE, f"agent-{ALICE}-chat-{sid}")}
    offer(store, sid, card("tell-other", "Summarise the board pack?"), card("tell-joe", f"Tell {JOE} it is ready?"))
    _refused(_notify(client, agent, sid))                                   # no press
    _refused(_notify(client, agent, sid, pressed="p-forged"))               # a forged press
    _refused(_notify(client, agent, sid, pressed=press(client, sid, "tell-other", "Yes", _h(ALICE))))
    pressed = press(client, sid, "tell-joe", "Yes", _h(ALICE))
    r = _notify(client, agent, sid, pressed=pressed)
    assert r.status_code == 200 and r.json()["to"] == [JOE], r.text
    assert store.proposal(pressed)["status"] == "spent"
    _refused(_notify(client, agent, sid, pressed=pressed, text="again"))    # one press, one notice


def test_the_agent_notifies_its_own_person_and_the_person_notifies_others_without_a_press(env):
    """The controls: a notice to the owner reaches nobody else; the person's own session is theirs."""
    client, store, _bus, _root = env
    sid = store.create(ALICE, name="team", title="Board prep")["id"]
    store.add_participant(sid, ALICE, JOE)
    agent = {**_h(ALICE), **_watching(ALICE, f"agent-{ALICE}-chat-{sid}")}
    assert client.post("/api/notifications/notify", headers=agent,
                       json={"text": "Done", "session": sid}).status_code == 200
    assert _notify(client, _h(ALICE), sid).status_code == 200


# ── message_agent: a message into another person's conversation ──────────────────────────────────

def test_an_agent_messages_another_persons_agent_only_on_the_persons_press(msg_world):
    client, store, mindex, published = msg_world
    _both_in(mindex)
    sid = _conversation(store)
    agent = _watching("alice", "agent-alice-chat-pchat-a")
    mine = store.by_name("alice", "pchat-a")["id"]
    _refused(_send(client, "alice", agent, thread=sid, person="bob", text="hi", from_thread="pchat-a"))
    assert published == []
    offer(store, mine, card("ask-bob", "Ask bob's agent for the figures?"))
    pressed = press(client, mine, "ask-bob", "Yes", {"X-User-Id": "alice", "x-user-credential-class": "session"})
    r = _send(client, "alice", agent, thread=sid, person="bob", text="hi", from_thread="pchat-a", pressed=pressed)
    assert r.status_code == 202, r.text
    _refused(_send(client, "alice", agent, thread=sid, person="bob", text="hi", from_thread="pchat-a",
                   pressed=pressed))
    assert len(published) == 1


def test_the_persons_own_message_needs_no_press(msg_world):
    client, store, mindex, published = msg_world
    _both_in(mindex)
    sid = _conversation(store)
    r = _send(client, "alice", thread=sid, person="bob", text="hi", from_thread="pchat-a")
    assert r.status_code == 202, r.text


# ── calendar: an event with attendees, or one that emails them ───────────────────────────────────

EVENT = {"request_id": "req-00000002", "title": "Review",
         "start": {"date_time": "2026-10-20T10:00:00", "time_zone": "Europe/Lisbon"},
         "end": {"date_time": "2026-10-20T10:30:00", "time_zone": "Europe/Lisbon"}}
READY = [{"id": "b" * 32, "provider": "google_calendar", "status": "ready", "label": ""}]


@pytest.fixture
def broker(monkeypatch):
    sent: list = []

    def fake(actor, method, path, payload=None, *, identity=None):
        if path == "/api/connections":
            return {"connections": READY}
        if path == "/api/preferences":
            return {}
        sent.append((path, payload))
        return {"status": "event_created"}

    monkeypatch.setattr(connections, "call_broker", fake)
    return sent


SIGNED = {"X-Vexa-Identity": "v1.signed-by-the-gateway.for-alice"}


def test_an_agent_invites_attendees_only_on_the_persons_press_naming_each(msg_world, broker):
    client, store, _mindex, _published = msg_world
    agent = {**_watching("alice", "agent-alice-chat-pchat-a"), **SIGNED}
    mine = store.by_name("alice", "pchat-a")["id"]
    body = {**EVENT, "attendees": ["ada@example.test", "bo@example.test"]}
    _refused(client.post("/api/connections/calendar/event/create", headers=agent, json=body))
    offer(store, mine, card("half", "Invite ada@example.test to Review?"),
          card("both", "Invite ada@example.test and bo@example.test to Review?"))
    at_terminal = {"X-User-Id": "alice", "x-user-credential-class": "session"}
    # a card that names only one of the two attendees reaches nobody
    _refused(client.post("/api/connections/calendar/event/create", headers=agent,
                         json={**body, "pressed": press(client, mine, "half", "Yes", at_terminal)}))
    assert not [p for p, _ in broker if p.endswith("/calendar-event")]
    r = client.post("/api/connections/calendar/event/create", headers=agent,
                    json={**body, "pressed": press(client, mine, "both", "Yes", at_terminal)})
    assert r.status_code == 200, r.text
    assert [p for p, _ in broker if p.endswith("/calendar-event")]


def test_an_event_for_the_person_alone_needs_no_press(msg_world, broker):
    """The control: an event that reaches nobody else is the agent's to make when asked."""
    client, _store, _mindex, _published = msg_world
    r = client.post("/api/connections/calendar/event/create",
                    headers={**_watching("alice", "agent-alice-chat-pchat-a"), **SIGNED}, json=EVENT)
    assert r.status_code == 200, r.text


# ── workspace_publish ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def publishing(monkeypatch):
    published: list = []
    monkeypatch.setattr(git_credentials, "read_token", lambda root, subject, provider: "saved-token")
    monkeypatch.setattr(ws_router, "publish_workspace", lambda *a, **kw: published.append(kw) or SimpleNamespace(
        repo_url="https://example.test/r", pushed_ref="main", head_sha="0" * 40, created=True,
        provider=kw.get("provider")))
    return published


def test_an_agent_publishes_only_on_the_persons_press(msg_world, publishing, tmp_path):
    client, store, _mindex, _published = msg_world
    agent = _watching("alice", "agent-alice-chat-pchat-a")
    mine = store.by_name("alice", "pchat-a")["id"]
    _refused(client.post("/api/workspace/publish", headers=agent, json={"repo_name": "team-notes"}))
    assert publishing == []
    offer(store, mine, card("pub", "Publish team-notes to your git account?"))
    pressed = press(client, mine, "pub", "Yes", {"X-User-Id": "alice", "x-user-credential-class": "session"})
    r = client.post("/api/workspace/publish", headers=agent, json={"repo_name": "team-notes", "pressed": pressed})
    assert r.status_code != 403 or r.json()["detail"].get("refused") != "press_required", r.text
    assert store.proposal(pressed)["status"] == "spent"
