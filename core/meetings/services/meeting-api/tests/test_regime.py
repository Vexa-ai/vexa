"""The verbs that need a person in the loop, refused to a worker dispatched without one.

A worker's signed identity carries its dispatch's regime (gateway-identity.v1 `delegation`). Putting
a bot into a meeting, sharing a transcript, binding a meeting to a shared workspace and deleting a
meeting or a recording run for a person's own credential, the internal tier, and a worker in the
`human` regime — never for an unwatched one (`meeting_api/regime.py`).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app, identity_token
from meeting_api.regime import REFUSAL

KEY = identity_token.generate_signing_key()
INTERNAL = "meeting-test-internal-secret"

GATED = [
    ("POST", "/bots", {"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}),
    ("DELETE", "/meetings/1", None),
    ("DELETE", "/meetings/google_meet/abc-defg-hij", None),
    ("DELETE", "/recordings/1", None),
    ("POST", "/meetings/1/share", {}),
    ("POST", "/meetings/google_meet/abc-defg-hij/share", {}),
    ("POST", "/meetings/google_meet/abc-defg-hij/workspace", {"workspace_id": "ws_1"}),
]


@pytest.fixture
def client():
    # A route that gets past the gate may fail further in on this bare app (no admin token to mint a
    # bot's meeting token); that is the route answering, which is all these tests need to see.
    return TestClient(create_app(identity_key=KEY.public_key(), internal_secret=INTERNAL),
                      raise_server_exceptions=False)


def _signed(**delegation) -> dict:
    claims = {"sub": "7", "scopes": ["bot", "tx"], "limits": 3, "workspaces": ["ws_1"]}
    if delegation:
        claims["delegation"] = delegation
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


def _send(client, method, path, body, headers):
    return client.request(method, path, headers=headers, json=body)


@pytest.mark.parametrize("method,path,body", GATED)
@pytest.mark.parametrize("regime", ["autonomous", "", "Human-ish"])
def test_an_unwatched_worker_is_refused(client, method, path, body, regime):
    r = _send(client, method, path, body,
              _signed(regime=regime, workspaces=["ws_1"], target="ws_1"))
    assert r.status_code == 403
    assert r.json()["detail"] == REFUSAL


@pytest.mark.parametrize("method,path,body", GATED)
def test_a_worker_with_its_person_in_the_chat_reaches_the_route(client, method, path, body):
    r = _send(client, method, path, body, _signed(regime="human", workspaces="*"))
    assert r.status_code != 403, r.text


@pytest.mark.parametrize("method,path,body", GATED)
def test_a_person_s_own_credential_and_the_internal_tier_reach_the_route(client, method, path, body):
    for headers in (_signed(), {"X-User-Id": "7", "X-Internal-Secret": INTERNAL}):
        r = _send(client, method, path, body, headers)
        assert r.status_code != 403, r.text


def test_reads_stay_open_to_an_unwatched_worker(client):
    r = client.get("/meetings", headers=_signed(regime="autonomous", workspaces=["ws_1"]))
    assert r.status_code == 200


@pytest.mark.parametrize("method,path,body", GATED)
def test_a_present_but_empty_regime_counts_as_delegated(client, method, path, body):
    """The rule agent-api's `ceiling.is_delegated` shares: the regime header present with nothing in
    it marks a delegated identity, and an empty regime is not `human`, so the verb is refused (the
    closed direction). The gateway never signs an empty regime; only the internal tier can send one."""
    headers = {"X-User-Id": "7", "X-Internal-Secret": INTERNAL, "x-user-regime": ""}
    r = _send(client, method, path, body, headers)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == REFUSAL
