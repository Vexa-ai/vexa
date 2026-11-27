"""POST /connect — the first route the edge forwards with NO key (identity's `app/connect.py`).

The raw ID token in the body is the only credential, and identity verifies it. So the edge forwards
this one route without `_authorize`: it caps the body and the calls from each address, strips every
header a client may not set, adds no identity header, and passes identity's answer on unchanged. These
tests also prove that nothing ELSE the edge serves is forwarded without a key.
"""
from __future__ import annotations

import json

from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from gateway import app as app_module
from gateway import create_app
from gateway.ratelimit import PerUserRateLimiter

from conftest import FakeAuthorizer, FakeDownstream, FakeRedis

BODY = {"provider": "google", "id_token": "eyJhbGciOiJSUzI1NiJ9.e30.c2lnbmF0dXJl"}


def _client(downstream=None, **kw):
    downstream = downstream or FakeDownstream(body={"user_id": 1, "key": "vxa_bot_x",
                                                    "key_expires_at": "2027-11-27T15:00:00Z", "bound": "first"})
    app = create_app(FakeAuthorizer(), downstream, FakeRedis(), admin_api_url="http://admin-api", **kw)
    return TestClient(app), downstream


def test_connect_is_forwarded_with_no_key_and_no_identity_header():
    client, down = _client()
    r = client.post("/connect", json=BODY)
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == 1
    assert down.last["method"] == "POST" and down.last["url"] == "http://admin-api/connect"
    assert json.loads(down.last["content"]) == BODY
    sent = {k.lower() for k in down.last["headers"]}
    assert "x-api-key" not in sent
    assert not any(h.startswith(("x-user-", "x-internal-", "x-vexa-")) for h in sent), sent


def test_identitys_answer_is_passed_on_unchanged():
    client, _ = _client(FakeDownstream(status_code=401, body={"detail": "the sign-in did not verify"}))
    r = client.post("/connect", json=BODY)
    assert r.status_code == 401 and r.json() == {"detail": "the sign-in did not verify"}


def test_no_other_forwarded_route_answers_without_a_key():
    """Every other route the edge serves, called with no key, sends nothing downstream."""
    client, down = _client()
    checked = 0
    for route in client.app.routes:
        if not isinstance(route, APIRoute) or route.path == "/connect":
            continue
        path = route.path
        for name in route.param_convertors:
            path = path.replace("{" + name + ":path}", "x").replace("{" + name + "}", "x")
        for method in sorted((route.methods or set()) - {"HEAD"}):
            down.last = None
            r = client.request(method, path, json={})
            assert down.last is None, f"{method} {route.path} forwarded with no key ({r.status_code})"
            checked += 1
    assert checked > 100


def test_a_body_over_the_cap_is_refused_before_identity_is_called():
    client, down = _client()
    big = {"provider": "google", "id_token": "x" * (app_module.CONNECT_MAX_BODY_BYTES + 1)}
    r = client.post("/connect", json=big)
    assert r.status_code == 413
    assert down.last is None


def test_a_call_with_no_body_is_refused_by_the_edge_not_a_500():
    client, down = _client()
    for kw in ({}, {"content": b""}, {"content": b"   "}):
        r = client.post("/connect", **kw)
        assert r.status_code == 400, (kw, r.status_code, r.text)
    assert down.last is None


def test_calls_from_one_address_are_capped():
    client, down = _client(connect_limiter=PerUserRateLimiter(capacity=2, refill_per_sec=0))
    assert client.post("/connect", json=BODY).status_code == 200
    assert client.post("/connect", json=BODY).status_code == 200
    down.last = None
    r = client.post("/connect", json=BODY)
    assert r.status_code == 429 and r.headers.get("retry-after")
    # A forwarded-for header from an untrusted peer does not buy a new budget.
    assert client.post("/connect", json=BODY, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 429
    assert down.last is None


def test_a_client_cannot_set_an_authority_header_on_connect():
    client, down = _client()
    spoof = {"X-Internal-Secret": "s", "X-User-Id": "1", "X-User-Email": "admin@x", "X-Vexa-Identity": "forged",
             "X-Admin-API-Key": "admin", "X-API-Key": "vxa_bot_someone", "Authorization": "Bearer t",
             "Cookie": "session=1", "X-Vexa-Internal-Hop": "1"}
    assert client.post("/connect", json=BODY, headers=spoof).status_code == 200
    sent = {k.lower() for k in down.last["headers"]}
    assert not sent & {k.lower() for k in spoof}, sent & {k.lower() for k in spoof}
