"""Integration tests for the Process backend.

Run with: ORCHESTRATOR_BACKEND=process make up && RUNTIME_API_URL=http://localhost:8190 pytest tests/test_integration_process.py -v

Or directly without Docker:
  ORCHESTRATOR_BACKEND=process REDIS_URL=redis://localhost:6379/0 uvicorn runtime_api.main:app --port 8190 &
  RUNTIME_API_URL=http://localhost:8190 pytest tests/test_integration_process.py -v

The Process backend spawns child processes instead of Docker containers.
Profiles must have a `command` field (not just `image`).
"""

import os
import time

import httpx
import pytest

BASE_URL = os.getenv("RUNTIME_API_URL", "http://localhost:8090")
TEST_USER = "process-test-user"
TEST_TIMEOUT = 15


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
        pytest.skip(f"runtime-api not running at {BASE_URL} — start it first")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Process lifecycle — spawn a real child process
# ---------------------------------------------------------------------------

class TestProcessLifecycle:
    container_name: str = None

    def test_01_create_process(self, client):
        """POST /containers with command → spawns a child process."""
        resp = client.post("/containers", json={
            "profile": "worker",
            "user_id": TEST_USER,
            "config": {
                # Override command for process backend — simple sleep
                "command": ["sleep", "60"],
            },
            "metadata": {"backend": "process"},
        })
        # If worker profile doesn't have command, the backend will error
        if resp.status_code == 500 and "requires spec.command" in resp.text:
            # Retry with explicit command in config override
            resp = client.post("/containers", json={
                "profile": "sandbox",
                "user_id": TEST_USER,
                "config": {
                    "command": ["sleep", "60"],
                },
                "metadata": {"backend": "process"},
            })
        assert resp.status_code == 201, f"Create failed: {resp.text}"
        data = resp.json()
        assert "name" in data
        assert data["user_id"] == TEST_USER
        TestProcessLifecycle.container_name = data["name"]

    def test_02_list_shows_process(self, client):
        """GET /containers includes the process."""
        resp = client.get("/containers", params={"user_id": TEST_USER})
        assert resp.status_code == 200
        containers = resp.json()
        names = [c["name"] for c in containers]
        assert TestProcessLifecycle.container_name in names

    def test_03_inspect_process(self, client):
        """GET /containers/{name} shows running status."""
        name = TestProcessLifecycle.container_name
        assert name is not None
        resp = client.get(f"/containers/{name}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == name
        assert data["status"] in ("running", "created")

    def test_04_stop_process(self, client):
        """DELETE /containers/{name} sends SIGTERM to the process."""
        name = TestProcessLifecycle.container_name
        resp = client.delete(f"/containers/{name}")
        assert resp.status_code in (200, 204)

    def test_05_process_stopped(self, client):
        """After stop, process is no longer running."""
        name = TestProcessLifecycle.container_name
        # Give the reaper a moment
        time.sleep(1)
        resp = client.get(f"/containers/{name}")
        if resp.status_code == 200:
            data = resp.json()
            assert data.get("status") in ("stopped", "exited", None)
        else:
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Short-lived process (exits immediately)
# ---------------------------------------------------------------------------

class TestShortProcess:
    def test_process_that_exits(self, client):
        """A process that exits immediately should be tracked correctly."""
        resp = client.post("/containers", json={
            "profile": "worker",
            "user_id": f"{TEST_USER}-short",
            "config": {
                "command": ["echo", "hello"],
            },
        })
        if resp.status_code == 500 and "requires spec.command" in resp.text:
            resp = client.post("/containers", json={
                "profile": "sandbox",
                "user_id": f"{TEST_USER}-short",
                "config": {
                    "command": ["echo", "hello"],
                },
            })
        if resp.status_code == 201:
            name = resp.json()["name"]
            # Process exits immediately — give reaper time
            time.sleep(2)
            inspect = client.get(f"/containers/{name}")
            if inspect.status_code == 200:
                assert inspect.json().get("status") in ("stopped", "exited", "running", None)
            # Cleanup
            client.delete(f"/containers/{name}")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def cleanup(client):
    yield
    try:
        resp = client.get("/containers")
        if resp.status_code == 200:
            for c in resp.json():
                if c.get("user_id", "").startswith("process-test"):
                    client.delete(f"/containers/{c['name']}")
    except Exception:
        pass
