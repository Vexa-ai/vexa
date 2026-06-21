"""Focused unit tests for the REST proxy half of ``create_app`` (with injected fakes).

Proves, in isolation from the conformance contract layer, the load-bearing carve of
``main.forward_request``:
  * fail-closed auth (no key → 401; bad key → 401),
  * scope 403 (a token lacking the route scope is rejected),
  * verbatim body + status passthrough on success,
  * identity headers injected downstream; client-supplied identity headers stripped.
"""
from fastapi.testclient import TestClient

from gateway import create_app
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

AUTH = {"x-api-key": VALID_KEY}


def _client(authorizer=None, downstream=None):
    downstream = downstream or FakeDownstream(status_code=200, body={"meetings": []})
    app = create_app(authorizer or FakeAuthorizer(), downstream, FakeRedis())
    return TestClient(app), downstream


def test_missing_api_key_is_401():
    client, _ = _client()
    r = client.get("/bots/status")
    assert r.status_code == 401
    assert r.json()["detail"] == "Missing API key"


def test_invalid_api_key_is_401():
    client, _ = _client()
    r = client.get("/bots/status", headers={"x-api-key": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid API key"


def test_insufficient_scope_is_403():
    """A tx-only token on a /bots route → 403 (ROUTE_SCOPES carve)."""
    client, _ = _client(authorizer=FakeAuthorizer(user={"user_id": 7, "scopes": ["tx"], "max_concurrent": 1}))
    r = client.get("/bots/status", headers=AUTH)
    assert r.status_code == 403
    assert "scope" in r.json()["detail"].lower()


def test_authed_request_passes_body_and_status_verbatim():
    """On success the downstream status + body are returned verbatim."""
    downstream = FakeDownstream(status_code=201, body={"id": 99, "platform": "google_meet"})
    client, _ = _client(downstream=downstream)
    r = client.post("/bots", headers=AUTH, json={"platform": "google_meet", "native_meeting_id": "abc"})
    assert r.status_code == 201
    assert r.json() == {"id": 99, "platform": "google_meet"}


def test_identity_headers_injected_and_spoof_stripped():
    """The gateway injects x-user-id from the resolved token and STRIPS client-supplied
    identity headers (anti-spoofing, main.py:294-296)."""
    client, downstream = _client()
    r = client.get("/bots/status", headers={**AUTH, "x-user-id": "999", "x-user-scopes": "admin"})
    assert r.status_code == 200
    fwd = downstream.last["headers"]
    assert fwd["x-user-id"] == "7", "must reflect the resolved user, not the spoofed header"
    assert fwd["x-user-scopes"] == "bot,tx,browser"
    assert fwd["x-api-key"] == VALID_KEY


def test_downstream_target_url_matches_route_table():
    """/transcripts forwards to the transcription-collector base; /bots to meeting-api."""
    client, downstream = _client()
    client.get("/transcripts/google_meet/abc", headers=AUTH)
    assert downstream.last["url"].endswith("/transcripts/google_meet/abc")
    assert "transcription-collector" in downstream.last["url"]
    client.get("/bots/status", headers=AUTH)
    assert "meeting-api" in downstream.last["url"]
