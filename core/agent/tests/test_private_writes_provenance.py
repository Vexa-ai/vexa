"""Friction 2 (app.dev 2026-10-16): the leak rule on writes holds what CAME FROM the private
sources, not every write of a session that read one.

The agent read the person's mail, then wrote a company page about CircleCI whose facts came from
nowhere private — held, twice. Deny stays deny: a write that carries what the mail said (its
amount, its sender's address, a sentence of it) is still refused with the confirmation code, and
names what came from the mail so the agent can leave exactly that out. A read the platform did not
see the answer of still holds every write (the rule before this one).
"""
from __future__ import annotations

import pytest

from control_plane import private_taint
from control_plane.routers import connections as conn_mod
from tests.test_private_writes_held import _worker, _write, setup  # noqa: F401 — the fixture

MAIL = {"messages": [{
    "id": "m1", "from": "Lena Fischer <lena.fischer@venue.example>",
    "subject": "Invoice 44719 overdue",
    "snippet": "Hi Ada, the venue invoice for the October meetup is still unpaid: €615 due Friday. "
               "Please approve the payment so we can hold the room.",
}]}


@pytest.fixture
def broker(monkeypatch):
    def fake(actor, method, path, payload=None, *, identity):
        if path == "/api/connections":
            return {"connections": [{"id": "c1", "provider": "google_email", "status": "ready"}]}
        if path == "/api/preferences":
            return {}
        if path.endswith("/read"):
            return MAIL
        raise AssertionError(path)
    monkeypatch.setattr(conn_mod, "call_broker", fake)


def _read_mail(client, unit):
    r = client.post("/api/connections/gmail/search", json={"query": "invoice", "limit": 5},
                    headers=_worker(unit))
    assert r.status_code == 200, r.text


def test_a_page_with_nothing_from_the_mail_is_written(setup, broker):
    client, ws, root = setup
    _read_mail(client, "u1")
    r = _write(client, "u1", ws, path="kg/entities/company/circleci.md",
               content="# CircleCI\n\nCI/CD service. We evaluate it for the build pipeline.\n")
    assert r.status_code == 200, r.text
    assert list(root.rglob("circleci.md"))


def test_a_page_that_carries_the_amount_is_held_and_says_what_came_from_the_mail(setup, broker):
    client, ws, root = setup
    _read_mail(client, "u1")
    r = _write(client, "u1", ws, content="# Meetup\n\nVenue: €615 still to pay.\n")
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["refused"] == "private_sources_to_shared" and len(d["confirm_code"]) == 6
    assert "615" in d["carried"]
    assert "615" in d["instruction"] and "do not say it was saved" in d["instruction"]
    assert not list(root.rglob("meetup.md"))


@pytest.mark.parametrize("content", [
    "Contact: lena.fischer@venue.example\n",                         # the sender's address
    "Invoice 44719 is open.\n",                                     # a long number
    "Talk to Lena Fischer about the room.\n",                       # a full name
    "the venue invoice for the October meetup is still unpaid\n",   # a sentence of it
])
def test_each_kind_of_private_detail_is_held(setup, broker, content):
    client, ws, _ = setup
    _read_mail(client, "u1")
    assert _write(client, "u1", ws, content=content).status_code == 409


def test_what_the_page_already_said_does_not_hold_the_write(setup, broker):
    client, ws, _ = setup
    assert _write(client, "u-clean", ws, content="# Meetup\n\nHost: Lena Fischer.\n").status_code == 200
    _read_mail(client, "u1")
    r = _write(client, "u1", ws, content="# Meetup\n\nHost: Lena Fischer.\nDate: to be set.\n")
    assert r.status_code == 200, r.text


def test_a_read_whose_answer_was_not_seen_holds_every_write(setup):
    client, ws, _ = setup
    private_taint.mark("u1", "connected service")          # content unknown
    r = _write(client, "u1", ws, path="kg/entities/company/circleci.md", content="# CircleCI\n")
    assert r.status_code == 409, r.text


def test_an_entity_with_facts_from_nowhere_private_is_written(setup, broker):
    client, ws, _ = setup
    _read_mail(client, "u1")
    r = client.post("/api/workspace/entity", json={
        "slug": ws, "kind": "company", "name": "CircleCI", "facts": ["CI/CD service"], "source": "web"},
        headers=_worker("u1"))
    assert r.status_code != 409, r.text


def test_the_private_answer_tells_the_agent_how_shared_writes_go(setup, broker):
    """Friction 1: told inside the turn that read it, not after the turn put a write back."""
    client, _, _ = setup
    r = client.post("/api/connections/gmail/search", json={"query": "invoice", "limit": 5},
                    headers=_worker("u1"))
    body = r.json()
    assert body["messages"] == MAIL["messages"]
    assert "workspace_write" in body["private_note"] and "approval" in body["private_note"]
