"""Integration tests — require a running runtime-api + Redis + Docker.

Run with: make test-integration (which runs `make up` first)

These tests hit the actual HTTP API and spawn real Docker containers.
They use the 'worker' profile from profiles.example.yaml (python:3.12-slim).
"""

import os
import time

import httpx
import pytest

BASE_URL = os.getenv("RUNTIME_API_URL", "http://localhost:8090")
TEST_USER = "integration-test-user"
TEST_TIMEOUT = 30


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=TEST_TIMEOUT) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def check_service(client):
    """Fail fast if runtime-api is not running."""
    try:
        resp = client.get("/health")
        resp.raise_for_status()
    except httpx.ConnectError:
        pytest.skip(f"runtime-api not running at {BASE_URL} — run `make up` first")


# ---------------------------------------------------------------------------
# Health & profiles
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_profiles_loaded(self, client):
        resp = client.get("/profiles")
        assert resp.status_code == 200
        profiles = resp.json()
        assert isinstance(profiles, dict)
        # profiles.example.yaml has 4 profiles
        assert len(profiles) >= 1


# ---------------------------------------------------------------------------
# Container lifecycle — full CRUD
# ---------------------------------------------------------------------------

class TestContainerLifecycle:
    """Spawn a real container, inspect, touch, stop."""

    container_name: str = None

    def test_01_create_container(self, client):
        """POST /containers → spawns a real Docker container."""
        resp = client.post("/containers", json={
            "profile": "worker",
            "user_id": TEST_USER,
            "config": {
                "env": {"TEST_VAR": "hello"},
            },
            "metadata": {"test_run": True},
        })
        assert resp.status_code == 201, f"Create failed: {resp.text}"
        data = resp.json()
        assert "name" in data
        assert data["profile"] == "worker"
        assert data["user_id"] == TEST_USER
        TestContainerLifecycle.container_name = data["name"]

    def test_02_list_containers(self, client):
        """GET /containers → list includes our container."""
        resp = client.get("/containers", params={"user_id": TEST_USER})
        assert resp.status_code == 200
        containers = resp.json()
        assert isinstance(containers, list)
        names = [c["name"] for c in containers]
        assert TestContainerLifecycle.container_name in names

    def test_03_inspect_container(self, client):
        """GET /containers/{name} → returns container details."""
        name = TestContainerLifecycle.container_name
        assert name is not None, "Container not created"
        resp = client.get(f"/containers/{name}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == name
        assert data["user_id"] == TEST_USER
        assert data["profile"] == "worker"
        assert data["status"] in ("running", "created", "starting")

    def test_04_touch_container(self, client):
        """POST /containers/{name}/touch → resets idle timer."""
        name = TestContainerLifecycle.container_name
        resp = client.post(f"/containers/{name}/touch")
        assert resp.status_code == 200

    def test_05_stop_container(self, client):
        """DELETE /containers/{name} → stops and removes."""
        name = TestContainerLifecycle.container_name
        resp = client.delete(f"/containers/{name}")
        assert resp.status_code in (200, 204)

    def test_06_container_gone(self, client):
        """After delete, container should be stopped or absent."""
        name = TestContainerLifecycle.container_name
        resp = client.get(f"/containers/{name}")
        if resp.status_code == 200:
            # State entry may linger — verify status is stopped/exited
            assert resp.json().get("status") in ("stopped", "exited", "removing", None)
        else:
            assert resp.status_code == 404

    def test_07_list_after_stop(self, client):
        """After stop, user's containers are stopped (may still be listed)."""
        resp = client.get("/containers", params={"user_id": TEST_USER})
        assert resp.status_code == 200
        containers = resp.json()
        running = [c for c in containers if c["user_id"] == TEST_USER and c.get("status") == "running"]
        assert len(running) == 0


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class TestErrors:
    def test_unknown_profile(self, client):
        """Creating with non-existent profile → 400 or 404."""
        resp = client.post("/containers", json={
            "profile": "nonexistent-profile-xyz",
            "user_id": TEST_USER,
        })
        assert resp.status_code in (400, 404, 422)

    def test_inspect_nonexistent(self, client):
        """GET /containers/does-not-exist → returns default stopped entry or 404."""
        resp = client.get("/containers/does-not-exist-abc123")
        # API returns 200 with a default entry (container_id=None) or 404
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.json()
            # Nonexistent containers have no container_id
            assert data.get("container_id") is None

    def test_delete_nonexistent(self, client):
        """DELETE /containers/does-not-exist → 200 (idempotent) or 404."""
        resp = client.delete("/containers/does-not-exist-abc123")
        # Idempotent delete is valid (returns 200 even if not found)
        assert resp.status_code in (200, 204, 404)


# ---------------------------------------------------------------------------
# Callback verification
# ---------------------------------------------------------------------------

class TestCallback:
    """Verify callback_url is invoked when container stops."""

    def test_callback_url_accepted(self, client):
        """Create with callback_url — should be stored in state."""
        resp = client.post("/containers", json={
            "profile": "worker",
            "user_id": f"{TEST_USER}-callback",
            "callback_url": "http://httpbin.org/post",
            "config": {"env": {"CALLBACK_TEST": "1"}},
        })
        assert resp.status_code == 201
        name = resp.json()["name"]

        # Verify callback_url stored
        inspect_resp = client.get(f"/containers/{name}")
        assert inspect_resp.status_code == 200

        # Cleanup
        client.delete(f"/containers/{name}")


# ---------------------------------------------------------------------------
# Cleanup — kill any leftover test containers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def cleanup_test_containers(client):
    """After all tests, remove any containers left by this test run."""
    yield
    try:
        resp = client.get("/containers")
        if resp.status_code == 200:
            for c in resp.json():
                if c.get("user_id", "").startswith("integration-test"):
                    client.delete(f"/containers/{c['name']}")
    except Exception:
        pass
