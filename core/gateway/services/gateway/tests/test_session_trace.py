"""The agent session rides the signed identity, and the edge logs under it (Vexa-ai/vexa#1783).

A worker's delegation token names the unit it was minted for; identity answers it as
``delegation.unit``. The edge signs it into the identity it forwards (``x-user-delegation-unit``
rebuilt by every verifier), logs every line of the request with ``session``, and continues the
caller's ``X-Trace-Id``. A client cannot set the session: the header is an authority header the edge
strips, and the edge's lines take it from identity's answer alone.
"""
from fastapi.testclient import TestClient

from gateway import create_app, identity_token, obs
from conftest import SIGNING_KEY, VERIFY_KEY, FakeAuthorizer, FakeDownstream, FakeRedis, VALID_KEY

DELEGATED = "vxd_header.payload.signature"
UNIT = "agent-42-chat-main"
WORKER = {"user_id": 42, "scopes": ["bot", "tx"], "max_concurrent": 3, "email": "ada@example.com",
          "delegation": {"regime": "human", "workspaces": "*", "unit": UNIT}}


def _client(user, key):
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(user=user, valid_key=key), downstream, FakeRedis(),
                     identity_key=SIGNING_KEY)
    return TestClient(app), downstream


def _captured(fn):
    sink: list = []
    obs.capture(sink)
    try:
        result = fn()
    finally:
        obs.capture(None)
    return result, sink


def test_a_workers_mcp_call_is_signed_and_logged_under_its_session_and_the_callers_trace():
    client, downstream = _client(WORKER, DELEGATED)
    r, lines = _captured(lambda: client.post("/mcp", json={}, headers={
        "Authorization": f"Bearer {DELEGATED}", "X-Trace-Id": "turn-trace-1",
        "X-User-Delegation-Unit": "agent-99-chat-forged"}))
    assert r.status_code == 200
    assert r.headers["x-trace-id"] == "turn-trace-1"
    fwd = downstream.last["headers"]
    claims = identity_token.verify(VERIFY_KEY, fwd[identity_token.HEADER])
    assert claims["delegation"]["unit"] == UNIT
    assert fwd["x-user-delegation-unit"] == UNIT
    assert fwd["x-trace-id"] == "turn-trace-1"
    assert lines and all(e["trace_id"] == "turn-trace-1" for e in lines)
    after_auth = [e for e in lines if e["event"] != "request_received"]
    assert after_auth and all(e.get("session") == UNIT for e in after_auth)
    assert not any("forged" in str(e) or DELEGATED in str(e) for e in lines)


def test_a_persons_own_request_carries_no_session_whatever_it_sends():
    person = {"user_id": 7, "scopes": ["bot", "tx"], "max_concurrent": 3, "email": "p@example.com"}
    client, downstream = _client(person, VALID_KEY)
    r, lines = _captured(lambda: client.get("/meetings", headers={
        "X-API-Key": VALID_KEY, "X-User-Delegation-Unit": "agent-99-chat-forged"}))
    assert r.status_code == 200
    assert "x-user-delegation-unit" not in downstream.last["headers"]
    assert lines and not any("session" in e for e in lines)
