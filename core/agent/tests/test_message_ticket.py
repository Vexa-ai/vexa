"""`thread.message` carries agent-api's event ticket for the person it is delivered to.

flows' `deliver_message` posts the message as a turn in `refs.uid`'s chat (`/api/chat/submit`), so
the ticket names that person — the target, never the sender."""
from __future__ import annotations

import pytest

from control_plane import subject_ticket, ticket_mint
from tests import _ticket_keys
from tests.test_agent_messages_store import _open, _send, world  # noqa: F401 — fixture


@pytest.fixture
def key(tmp_path, monkeypatch):
    return _ticket_keys.install(tmp_path, monkeypatch)


def test_a_message_into_another_persons_chat_carries_a_ticket_for_them(world, key):
    client, store, published = world
    _open(client, "alice", "pchat-a")
    shared = store.create("bob", name="pchat-team", title="team")
    store.add_participant(shared["id"], "bob", "alice", role="member")
    r = _send(client, "alice", thread=shared["id"], person="bob", text="hi", from_thread="pchat-a")
    assert r.status_code == 202, r.text
    sid, refs = published[-1]
    [ticket] = subject_ticket.tickets_of(refs)
    claims = subject_ticket.read(ticket, _ticket_keys.checks(key))
    assert claims["iss"] == "agent" and claims["kind"] == "event"
    assert claims["sub"] == "bob" == refs["uid"]
    assert claims["cls"] == ["routine.turn"]
    assert claims["evt"] == sid
    assert claims["exp"] - claims["iat"] == 86400


def test_a_message_to_ones_own_chat_names_the_sender(world, key):
    client, _, published = world
    _open(client, "alice", "pchat-a")
    _open(client, "alice", "pchat-b")
    assert _send(client, "alice", thread="pchat-b", text="x",
                 from_thread="pchat-a").status_code == 202
    [ticket] = subject_ticket.tickets_of(published[-1][1])
    assert subject_ticket.read(ticket, _ticket_keys.checks(key))["sub"] == "alice"


def test_no_key_sends_the_message_without_a_ticket(world, monkeypatch):
    monkeypatch.delenv(ticket_mint.KEY_ENV, raising=False)
    monkeypatch.setattr(ticket_mint, "_signers", {})
    client, _, published = world
    _open(client, "alice", "pchat-a")
    _open(client, "alice", "pchat-b")
    assert _send(client, "alice", thread="pchat-b", text="x",
                 from_thread="pchat-a").status_code == 202
    assert subject_ticket.REFS_KEY not in published[-1][1]
