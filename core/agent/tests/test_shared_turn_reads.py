"""ADR-0048 decision 4, past the workspaces: a turn other people read reaches nothing person-wide.

A worker whose turn answers into a shared conversation carries `delegation.shared` on its signed
identity (`x-user-delegation-shared: 1`). agent-api refuses it the person's mail, calendar, other
connections, onboarding research over them, and the meeting routes — with the one body every service
answers (`identity_token.SHARED_REFUSAL`), before anything reaches the credential broker. The same
worker in a private turn, and the person's own client, are not refused by this.
"""
from __future__ import annotations

import pytest

from control_plane import identity_token
from tests.test_delegation_ceiling_verbs import KEY, PERSON, _build

SHARED = identity_token.SHARED_REFUSAL["reason"]

PERSON_WIDE = [
    ("GET", "/api/connections", None),
    ("POST", "/api/connections/gmail/search", {"query": "from:someone", "limit": 5}),
    ("POST", "/api/connections/gmail/inbox", {"limit": 5}),
    ("POST", "/api/connections/gmail/read", {"message_id": "m1"}),
    ("POST", "/api/connections/calendar/events", {"time_min": "2026-10-01T00:00:00Z",
                                                  "time_max": "2026-10-02T00:00:00Z"}),
    ("POST", "/api/connections/service/call", {"connection_id": "c1", "parameters": {}}),
    ("POST", "/api/meeting/terms/scan", {}),
]


def _worker(*, shared: bool) -> dict:
    dlg = {"regime": "human", "workspaces": ["team"]}
    if shared:
        dlg["shared"] = True
    claims = {"sub": PERSON, "email": "ada@example.com", "delegation": dlg}
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from control_plane.routers import connections

    calls = []

    def _broker(*a, **k):
        calls.append(a)
        raise AssertionError("a shared turn must not reach the broker")

    monkeypatch.setattr(connections, "call_broker", _broker, raising=False)
    c = _build(monkeypatch, tmp_path)
    c.broker_calls = calls
    return c


@pytest.mark.parametrize("method,path,body", PERSON_WIDE)
def test_a_shared_turn_is_refused_every_person_wide_read(client, method, path, body):
    r = client.request(method, path, json=body, headers=_worker(shared=True))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["reason"] == SHARED
    assert client.broker_calls == []


@pytest.mark.parametrize("method,path,body", PERSON_WIDE)
def test_a_private_turn_is_not_refused_by_the_shared_rule(client, method, path, body):
    r = client.request(method, path, json=body, headers=_worker(shared=False))
    detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else None
    assert not (isinstance(detail, dict) and detail.get("reason") == SHARED)
