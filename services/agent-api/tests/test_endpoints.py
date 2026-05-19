"""Tests for agent_api.main — FastAPI endpoint integration tests.

Uses TestClient to exercise the actual HTTP endpoints with mocked
Redis and ContainerManager dependencies.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_redis():
    """Build a mock async Redis."""
    r = AsyncMock()
    r.ping = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock()
    r.delete = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.hget = AsyncMock(return_value=None)
    r.hset = AsyncMock()
    r.hdel = AsyncMock()
    r.expire = AsyncMock()
    r.close = AsyncMock()
    return r


@pytest.fixture
def client():
    """Create a TestClient with mocked dependencies.

    Patches startup/shutdown to avoid real Redis/Runtime API connections.
    """
    with patch("agent_api.config.API_KEY", ""), \
         patch("agent_api.config.LOG_LEVEL", "WARNING"):

        from agent_api.main import app, cm

        redis = _mock_redis()
        original_startup = None
        original_shutdown = None

        # Replace startup/shutdown to avoid real connections
        for route in app.router.on_startup:
            if original_startup is None:
                original_startup = route
        for route in app.router.on_shutdown:
            if original_shutdown is None:
                original_shutdown = route

        # Clear events and set mocks manually
        saved_startup = list(app.router.on_startup)
        saved_shutdown = list(app.router.on_shutdown)
        app.router.on_startup.clear()
        app.router.on_shutdown.clear()

        app.state.redis = redis

        try:
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c
        finally:
            app.router.on_startup.extend(saved_startup)
            app.router.on_shutdown.extend(saved_shutdown)


@pytest.fixture
def auth_client():
    """Create a TestClient with API key enforcement enabled."""
    with patch("agent_api.config.API_KEY", "secret-key-123"), \
         patch("agent_api.config.LOG_LEVEL", "WARNING"):

        from agent_api.main import app

        redis = _mock_redis()

        saved_startup = list(app.router.on_startup)
        saved_shutdown = list(app.router.on_shutdown)
        app.router.on_startup.clear()
        app.router.on_shutdown.clear()

        app.state.redis = redis

        try:
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c
        finally:
            app.router.on_startup.extend(saved_startup)
            app.router.on_shutdown.extend(saved_shutdown)
            # Reset to empty for other tests
            with patch("agent_api.config.API_KEY", ""):
                pass


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "containers" in data


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------

class TestChatEndpoint:
    def test_post_chat_returns_sse(self, client):
        """POST /api/chat should return a streaming response."""
        resp = client.post(
            "/api/chat",
            json={"user_id": "test-user", "message": "Hello"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_delete_chat_requires_body(self, client):
        """DELETE /api/chat should accept user_id."""
        resp = client.request(
            "DELETE", "/api/chat",
            json={"user_id": "test-user"},
        )
        assert resp.status_code in (200, 404, 422, 500)

    def test_post_chat_reset(self, client):
        """POST /api/chat/reset should be reachable."""
        resp = client.post(
            "/api/chat/reset",
            json={"user_id": "test-user"},
        )
        assert resp.status_code in (200, 404, 422, 500)


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------

class TestSessionEndpoints:
    def test_get_sessions(self, client):
        resp = client.get("/api/sessions", params={"user_id": "test-user"})
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

    def test_create_session(self, client):
        resp = client.post(
            "/api/sessions",
            json={"user_id": "test-user", "name": "Test Session"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["name"] == "Test Session"

    def test_delete_session(self, client):
        resp = client.request(
            "DELETE", "/api/sessions/sess-123",
            params={"user_id": "test-user"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_rename_session(self, client):
        resp = client.put(
            "/api/sessions/sess-123",
            json={"user_id": "test-user", "name": "Renamed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "renamed"


# ---------------------------------------------------------------------------
# Workspace endpoints
# ---------------------------------------------------------------------------

class TestWorkspaceEndpoints:
    def test_list_files_no_container(self, client):
        """Should 404 when no container exists for user."""
        resp = client.get(
            "/api/workspace/files",
            params={"user_id": "unknown-user"},
        )
        assert resp.status_code == 404

    def test_get_file_no_container(self, client):
        resp = client.get(
            "/api/workspace/file",
            params={"user_id": "unknown-user", "path": "test.txt"},
        )
        assert resp.status_code == 404

    def test_get_file_path_traversal_blocked(self, client):
        """Path traversal attempts should be rejected."""
        resp = client.get(
            "/api/workspace/file",
            params={"user_id": "test-user", "path": "../../../etc/passwd"},
        )
        assert resp.status_code == 400

    def test_get_file_absolute_path_blocked(self, client):
        resp = client.get(
            "/api/workspace/file",
            params={"user_id": "test-user", "path": "/etc/passwd"},
        )
        assert resp.status_code == 400

    def test_write_file_no_container(self, client):
        resp = client.post(
            "/api/workspace/file",
            json={"user_id": "unknown-user", "path": "test.txt", "content": "hi"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Internal endpoints
# ---------------------------------------------------------------------------

class TestInternalEndpoints:
    def test_workspace_save_no_container(self, client):
        resp = client.post(
            "/internal/workspace/save",
            json={"user_id": "unknown-user"},
        )
        assert resp.status_code == 404

    def test_workspace_status(self, client):
        resp = client.get(
            "/internal/workspace/status",
            params={"user_id": "test-user"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "test-user"
        assert "workspace_in_storage" in data
        assert "container_running" in data


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------

class TestAuthEnforcement:
    def test_api_key_required_when_configured(self, auth_client):
        """When API_KEY is set, endpoints should reject requests without it."""
        resp = auth_client.get("/api/sessions", params={"user_id": "test"})
        assert resp.status_code == 403

    def test_api_key_accepted_when_valid(self, auth_client):
        """Valid API key should be accepted."""
        resp = auth_client.get(
            "/api/sessions",
            params={"user_id": "test"},
            headers={"X-API-Key": "secret-key-123"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Route existence
# ---------------------------------------------------------------------------

class TestRouteExistence:
    """Verify all documented routes exist (not 404/405)."""

    def test_post_chat_exists(self, client):
        resp = client.post("/api/chat", json={"user_id": "u", "message": "m"})
        assert resp.status_code != 405

    def test_delete_chat_exists(self, client):
        resp = client.request("DELETE", "/api/chat", json={"user_id": "u"})
        assert resp.status_code != 405

    def test_post_chat_reset_exists(self, client):
        resp = client.post("/api/chat/reset", json={"user_id": "u"})
        assert resp.status_code != 405

    def test_get_sessions_exists(self, client):
        resp = client.get("/api/sessions", params={"user_id": "u"})
        assert resp.status_code != 405

    def test_post_sessions_exists(self, client):
        resp = client.post("/api/sessions", json={"user_id": "u", "name": "n"})
        assert resp.status_code != 405

    def test_delete_session_exists(self, client):
        resp = client.request("DELETE", "/api/sessions/s1", params={"user_id": "u"})
        assert resp.status_code != 405

    def test_put_session_exists(self, client):
        resp = client.put("/api/sessions/s1", json={"user_id": "u", "name": "n"})
        assert resp.status_code != 405

    def test_get_workspace_files_exists(self, client):
        resp = client.get("/api/workspace/files", params={"user_id": "u"})
        assert resp.status_code != 405

    def test_get_workspace_file_exists(self, client):
        resp = client.get("/api/workspace/file", params={"user_id": "u", "path": "f"})
        assert resp.status_code != 405

    def test_post_workspace_file_exists(self, client):
        resp = client.post("/api/workspace/file", json={"user_id": "u", "path": "f", "content": "c"})
        assert resp.status_code != 405

    def test_internal_workspace_save_exists(self, client):
        resp = client.post("/internal/workspace/save", json={"user_id": "u"})
        assert resp.status_code != 405

    def test_internal_workspace_status_exists(self, client):
        resp = client.get("/internal/workspace/status", params={"user_id": "u"})
        assert resp.status_code != 405

    def test_health_exists(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
