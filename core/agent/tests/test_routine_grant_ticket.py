"""A routine the person arms from their own session carries agent-api's signed routine grant.

The gateway door lets flows run a routine's turns and bots for a person only on a grant agent-api
signed (`subject_ticket`, iss=agent, kind=grant). Verified here with the PUBLIC key, as the door
does. A key or a delegated turn files a draft and carries none.
"""
from __future__ import annotations

import json

import pytest

from control_plane import routine_flows as rf
from control_plane import subject_ticket, ticket_mint
from tests import _ticket_keys
from tests.test_routine_flows import FakeFlows, _as, _session, world  # noqa: F401 — fixtures


class GrantFlows(FakeFlows):
    def __init__(self):
        super().__init__()
        self.grants: list = []

    def submit(self, subject, name, cron, instruction, *, activate=True, grant=""):
        self.grants.append(("submit", grant))
        return super().submit(subject, name, cron, instruction, activate=activate)

    def set_status(self, subject, name, version, action, *, grant=""):
        self.grants.append((action, grant))
        return super().set_status(subject, name, version, action)


@pytest.fixture
def flows(monkeypatch):
    fake = GrantFlows()
    monkeypatch.setattr(rf, "FlowsClient", lambda: fake)
    monkeypatch.setattr(rf, "configured", lambda: True)
    return fake


@pytest.fixture
def key(tmp_path, monkeypatch):
    return _ticket_keys.install(tmp_path, monkeypatch)


def _claims(token, key):
    return subject_ticket.read(token, _ticket_keys.checks(key))


def _assert_grant(claims, name):
    assert claims["iss"] == "agent" and claims["kind"] == "grant"
    assert claims["sub"] == "7"
    assert claims["evt"] == f"user:7/{name}"
    assert set(claims["cls"]) == {"routine.turn", "bot.dispatch"}
    assert claims["exp"] - claims["iat"] == 400 * 86400


def test_a_session_creating_a_routine_on_sends_its_grant(world, flows, key):
    client, _, _ = world
    r = client.post("/api/routines", headers=_session(),
                    json={"name": "Morning brief", "cron": "0 8 * * *", "prompt": "brief me"})
    assert r.status_code == 201, r.text
    assert flows.rows[-1]["status"] == "active"
    [(op, grant)] = flows.grants
    assert op == "submit" and grant
    _assert_grant(_claims(grant, key), "Morning-brief")


def test_a_key_or_a_delegated_turn_files_a_draft_with_no_grant(world, flows, key):
    client, _, _ = world
    for i, regime in enumerate((None, "human")):
        r = client.post("/api/routines", headers=_as(regime),
                        json={"name": f"r{i}", "cron": "0 8 * * *", "prompt": "x"})
        assert r.status_code == 201, r.text
        assert flows.rows[-1]["status"] == "draft"
    assert flows.grants == [("submit", ""), ("submit", "")]


def test_the_persons_turn_on_sends_a_grant_and_a_pause_does_not(world, flows, key):
    client, _, _ = world
    client.post("/api/routines", headers=_as(),
                json={"name": "Morning brief", "cron": "0 8 * * *", "prompt": "brief me"})
    r = client.patch("/api/routines/Morning-brief/enabled", headers=_session(),
                     json={"enabled": True})
    assert r.status_code == 200, r.text
    op, grant = flows.grants[-1]
    assert op == "resume"
    _assert_grant(_claims(grant, key), "Morning-brief")
    client.patch("/api/routines/Morning-brief/enabled", headers=_session(), json={"enabled": False})
    assert flows.grants[-1] == ("pause", "")


def test_a_refused_turn_on_mints_nothing(world, flows, key):
    client, _, _ = world
    client.post("/api/routines", headers=_as(),
                json={"name": "Morning brief", "cron": "0 8 * * *", "prompt": "brief me"})
    r = client.patch("/api/routines/Morning-brief/enabled", headers=_as(),
                     json={"enabled": True})
    assert r.status_code == 403
    assert [op for op, _ in flows.grants] == ["submit"]


def test_no_key_arms_the_routine_without_a_grant(world, flows, monkeypatch, caplog):
    monkeypatch.delenv(ticket_mint.KEY_ENV, raising=False)
    monkeypatch.setattr(ticket_mint, "_signers", {})
    client, _, _ = world
    with caplog.at_level("WARNING"):
        r = client.post("/api/routines", headers=_session(),
                        json={"name": "Morning brief", "cron": "0 8 * * *", "prompt": "x"})
    assert r.status_code == 201 and flows.rows[-1]["status"] == "active"
    assert flows.grants == [("submit", "")]
    assert any("subject_ticket_key_missing" in m and "subject_ticket_required" in m
               for m in caplog.messages)


# ── the wire: what FlowsClient puts on flows-api's door ────────────────────────────────────────
class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


@pytest.fixture
def wire(monkeypatch):
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append({"url": req.full_url, "method": req.get_method(),
                     "headers": {k.lower(): v for k, v in req.header_items()},
                     "body": json.loads(req.data) if req.data else None})
        return _Resp()

    monkeypatch.setattr(rf.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_submit_carries_the_grant_as_a_body_field(wire):
    rf.FlowsClient(base="http://flows", key="op").submit("7", "brief", "0 8 * * *", "x",
                                                         grant="st1.a.b")
    assert wire[0]["body"]["grant"] == "st1.a.b" and wire[0]["body"]["activate"] is True
    rf.FlowsClient(base="http://flows", key="op").submit("7", "brief", "0 8 * * *", "x",
                                                         activate=False)
    assert "grant" not in wire[1]["body"]


def test_resume_carries_the_grant_in_the_body_and_the_header(wire):
    rf.FlowsClient(base="http://flows", key="op").set_status("7", "brief", 2, "resume",
                                                             grant="st1.a.b")
    assert wire[0]["url"].startswith("http://flows/flows/brief/2/resume?")
    assert wire[0]["body"] == {"grant": "st1.a.b"}
    assert wire[0]["headers"][ticket_mint.GRANT_HEADER.lower()] == "st1.a.b"
    rf.FlowsClient(base="http://flows", key="op").set_status("7", "brief", 2, "pause")
    assert wire[1]["body"] is None
    assert ticket_mint.GRANT_HEADER.lower() not in wire[1]["headers"]


def test_a_converted_routine_file_runs_under_a_grant_only_when_a_person_stood_behind_it(key):
    """The migration turns a legacy routine on only on agent-api's own evidence that a person stood
    behind the file; that is the evidence its grant rests on. A paused conversion carries none."""
    _assert_grant(_claims(rf._migration_grant("7", "daily", True), key), "daily")
    assert rf._migration_grant("7", "daily", False) == ""
