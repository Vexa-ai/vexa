"""Stage 2 (P20) — the gateway fronts the AGENT control plane (agent-api) under /agent/*.

Proves, in isolation from agent-api (injected FakeDownstream), the load-bearing guarantees:
  * fail-closed auth — a keyless agent call is 401 BEFORE any downstream hop;
  * the request is forwarded to agent-api's matching /api/<path> (path + method verbatim);
  * X-User-Id is injected from the RESOLVED key — never trusted from the client (anti-spoof),
    so agent-api (Stage 1) scopes `subject` to the authenticated user;
  * chat (SSE) is STREAMED, not buffered, and carries the same injected identity.
This is the seam every agent surface (sessions · routines · workspace · chat) rides for per-user scope.
"""
from fastapi.testclient import TestClient

from gateway import create_app
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

AUTH = {"x-api-key": VALID_KEY}


def _client(downstream=None):
    downstream = downstream or FakeDownstream(status_code=200, body={"sessions": []})
    app = create_app(FakeAuthorizer(), downstream, FakeRedis(), agent_api_url="http://agent-api")
    return TestClient(app), downstream


def test_keyless_agent_call_is_401_before_downstream():
    client, downstream = _client()
    r = client.get("/agent/sessions")
    assert r.status_code == 401
    assert r.json()["detail"] == "Missing API key"
    assert downstream.last is None, "must reject before any downstream hop"


def test_invalid_key_is_401():
    client, _ = _client()
    r = client.get("/agent/sessions", headers={"x-api-key": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid API key"


def test_agent_route_forwards_to_agent_api_with_injected_user():
    """A GET /api/sessions reaches agent-api's /api/sessions, body+status verbatim, X-User-Id injected."""
    client, downstream = _client(FakeDownstream(status_code=200, body={"sessions": [{"id": "s1"}]}))
    r = client.get("/agent/sessions", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"sessions": [{"id": "s1"}]}
    assert downstream.last["url"] == "http://agent-api/api/sessions"
    assert downstream.last["headers"]["x-user-id"] == "7"  # the resolved user, not the client's


def test_nested_agent_path_and_query_carry_through():
    """Workspace tree is a nested path with a query — both forward verbatim under /api/."""
    client, downstream = _client()
    client.get("/agent/workspace/tree?hidden=1", headers=AUTH)
    assert downstream.last["url"] == "http://agent-api/api/workspace/tree"
    assert downstream.last["params"] == {"hidden": "1"}
    assert downstream.last["headers"]["x-user-id"] == "7"


def test_client_supplied_user_id_is_stripped_then_reinjected():
    """A spoofed X-User-Id is dropped; the gateway re-injects the RESOLVED user (anti-spoof)."""
    client, downstream = _client()
    client.post("/agent/routines", headers={**AUTH, "x-user-id": "999"}, json={"name": "x"})
    assert downstream.last["headers"]["x-user-id"] == "7"


def test_agent_write_methods_forward():
    """POST/PUT/DELETE reach agent-api too (routine enable, session reset, workspace write)."""
    client, downstream = _client()
    client.put("/agent/routines/daily/enabled", headers=AUTH, json={"enabled": True})
    assert downstream.last["method"] == "PUT"
    assert downstream.last["url"] == "http://agent-api/api/routines/daily/enabled"


def test_agent_patch_method_forwards():
    """PATCH reaches agent-api too — the Routines surface toggles enable/disable via PATCH
    (routinesApi.setRoutineEnabled), and agent-api defines @app.patch(.../enabled). Regression:
    PATCH was absent from the proxy's methods list, so the toggle 405'd before any downstream hop."""
    client, downstream = _client()
    client.patch("/agent/routines/daily/enabled", headers=AUTH, json={"enabled": False})
    assert downstream.last["method"] == "PATCH"
    assert downstream.last["url"] == "http://agent-api/api/routines/daily/enabled"
    assert downstream.last["headers"]["x-user-id"] == "7"  # resolved user, injected downstream


def test_chat_is_streamed_not_buffered_with_injected_user():
    """POST /api/chat returns an SSE stream (text/event-stream), relays the downstream chunks, and
    carries the injected X-User-Id (so the streamed turn is scoped to the authenticated user)."""
    client, downstream = _client(FakeDownstream(stream_chunks=[
        b'data: {"type":"token","text":"he"}\n\n',
        b'data: {"type":"token","text":"llo"}\n\n',
        b'data: {"type":"done"}\n\n',
    ]))
    r = client.post("/agent/chat", headers=AUTH, json={"prompt": "hi", "session": "s1"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = r.text
    assert '"text":"he"' in body and '"text":"llo"' in body and '"type":"done"' in body
    assert downstream.last["url"] == "http://agent-api/api/chat"
    assert downstream.last["headers"]["x-user-id"] == "7"


def test_chat_keyless_is_401():
    client, downstream = _client()
    r = client.post("/agent/chat", json={"prompt": "hi"})
    assert r.status_code == 401
    assert downstream.last is None


def test_meeting_stream_is_streamed_with_injected_user():
    """GET /api/meeting/stream (the live transcript+copilot SSE) is streamed (not buffered by the
    catch-all) and carries the injected X-User-Id, with its query (meeting_id/session_uid) forwarded."""
    client, downstream = _client(FakeDownstream(stream_chunks=[
        b'data: {"type":"transcript","text":"hello"}\n\n',
        b'data: {"type":"copilot","text":"note"}\n\n',
    ]))
    r = client.get("/agent/meeting/stream?meeting_id=m1&session_uid=s1", headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert '"type":"transcript"' in r.text and '"type":"copilot"' in r.text
    assert downstream.last["url"] == "http://agent-api/api/meeting/stream"
    assert downstream.last["params"] == {"meeting_id": "m1", "session_uid": "s1"}
    assert downstream.last["headers"]["x-user-id"] == "7"
