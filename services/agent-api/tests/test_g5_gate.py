"""G5 gate — agent-api session lifecycle and auth against real Redis.

Run with:
    docker run --rm -d --name redis-g5 -p 6399:6379 redis:7-alpine
    docker build -f services/agent-api/Dockerfile -t agent-api:g5 .
    docker run --rm -d --name agent-api-g5 --network host \
      -e REDIS_URL=redis://localhost:6399/0 \
      -e RUNTIME_API_URL=http://localhost:8090 \
      -e API_KEY=test-key -e LOG_LEVEL=DEBUG -e STORAGE_BACKEND=local \
      agent-api:g5
    pytest services/agent-api/tests/test_g5_gate.py -v

Results (2026-03-27):

Test 1 — Session CRUD
  POST /api/sessions  {"user_id":"g5-test-user"}
    → 200 {"session_id":"2f6c421e-...","name":"New session"}                     PASS
  GET  /api/sessions?user_id=g5-test-user
    → 200 {"sessions":[{"id":"2f6c421e-...","name":"New session",...}]}           PASS
  DELETE /api/sessions/2f6c421e-...?user_id=g5-test-user
    → 200 {"status":"deleted"}                                                   PASS
  GET  /api/sessions?user_id=g5-test-user  (after delete)
    → 200 {"sessions":[]}                                                        PASS

Test 2 — Chat (no agent container)
  POST /api/chat  {"user_id":"g5-test-user","message":"hello"}
    → 200 SSE: {"type":"reconnecting"} then {"type":"error","message":"All connection attempts failed"}
    (graceful degradation, no crash)                                             PASS

Test 3 — Workspace (no container)
  GET  /api/workspace/files?user_id=g5-test-user
    → 404 {"detail":"No container for user g5-test-user"}                        PASS

Test 4 — Auth enforcement
  GET  /api/sessions?user_id=g5-test-user  (no key)
    → 403 {"detail":"Invalid or missing API key"}                                PASS
  GET  /api/sessions?user_id=g5-test-user  (wrong key)
    → 403 {"detail":"Invalid or missing API key"}                                PASS

Redis verification:
  redis-cli keys '*g5*' → agent:sessions:g5-test-user                           PASS

Summary: 8/8 PASS — G5 gate cleared.
"""
import os
import uuid

import httpx
import pytest

BASE = os.getenv("AGENT_API_URL", "http://localhost:8100")
API_KEY = os.getenv("API_KEY", "test-key")
USER = f"g5-{uuid.uuid4().hex[:8]}"
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=15) as c:
        yield c


# ── Health ────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── Session CRUD ──────────────────────────────────────────────────────────

def test_create_session(client):
    r = client.post("/api/sessions", headers=HEADERS, json={"user_id": USER})
    assert r.status_code == 200
    body = r.json()
    assert "session_id" in body
    assert body["name"] == "New session"
    # stash for later tests
    test_create_session.sid = body["session_id"]


def test_list_sessions(client):
    r = client.get("/api/sessions", headers=HEADERS, params={"user_id": USER})
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    assert len(sessions) >= 1
    assert any(s["id"] == test_create_session.sid for s in sessions)


def test_delete_session(client):
    sid = test_create_session.sid
    r = client.delete(f"/api/sessions/{sid}", headers=HEADERS, params={"user_id": USER})
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"


def test_list_sessions_after_delete(client):
    r = client.get("/api/sessions", headers=HEADERS, params={"user_id": USER})
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    assert not any(s["id"] == test_create_session.sid for s in sessions)


# ── Chat graceful degradation ────────────────────────────────────────────

def test_chat_no_container(client):
    """Chat should return SSE stream with error, not crash."""
    r = client.post(
        "/api/chat", headers=HEADERS,
        json={"user_id": USER, "message": "hello"},
    )
    assert r.status_code == 200
    text = r.text
    assert "error" in text


# ── Workspace graceful degradation ───────────────────────────────────────

def test_workspace_no_container(client):
    r = client.get(
        "/api/workspace/files", headers=HEADERS,
        params={"user_id": USER},
    )
    assert r.status_code == 404
    assert "No container" in r.json()["detail"]


# ── Auth enforcement ─────────────────────────────────────────────────────

def test_no_api_key(client):
    r = client.get("/api/sessions", params={"user_id": USER})
    assert r.status_code == 403


def test_wrong_api_key(client):
    r = client.get(
        "/api/sessions",
        headers={"X-API-Key": "wrong"},
        params={"user_id": USER},
    )
    assert r.status_code == 403
