"""G5 gate — callback delivery verified end-to-end.

Requires:
  - runtime-api running (RUNTIME_API_URL env, default http://localhost:8190)
  - ALLOW_PRIVATE_CALLBACKS=1 on the runtime-api instance
  - Docker daemon accessible to runtime-api
  - alpine:latest pulled

Run:
  RUNTIME_API_URL=http://localhost:8190 pytest tests/test_gate_g5.py -v
"""

import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import httpx
import pytest

BASE_URL = os.getenv("RUNTIME_API_URL", "http://localhost:8190")
TEST_USER = "g5-test"
TEST_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Callback receiver
# ---------------------------------------------------------------------------

class _CallbackCollector:
    """Thread-safe HTTP server that collects POST payloads."""

    def __init__(self, port: int = 19876):
        self.port = port
        self.received: list[dict] = []
        self._server: HTTPServer | None = None
        self._thread: Thread | None = None

    def start(self):
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                collector.received.append(body)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        self._server = HTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()

    def wait_for(self, count: int = 1, timeout: float = 15) -> list[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline and len(self.received) < count:
            time.sleep(0.3)
        return list(self.received)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=TEST_TIMEOUT) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def check_service(client):
    try:
        resp = client.get("/health")
        resp.raise_for_status()
    except httpx.ConnectError:
        pytest.skip(f"runtime-api not running at {BASE_URL}")


def _gateway_ip() -> str:
    """Resolve the Docker network gateway so containers can POST back to the host."""
    try:
        out = subprocess.check_output(
            ["docker", "network", "inspect", "runtime-api_default",
             "-f", "{{range .IPAM.Config}}{{.Gateway}}{{end}}"],
            text=True,
        ).strip()
        if out:
            return out
    except Exception:
        pass
    return "172.17.0.1"


@pytest.fixture(scope="module")
def callback():
    cb = _CallbackCollector(port=19876)
    cb.start()
    yield cb
    cb.stop()


@pytest.fixture(scope="module")
def callback_url(callback):
    gw = _gateway_ip()
    return f"http://{gw}:{callback.port}/exit"


@pytest.fixture(scope="module", autouse=True)
def cleanup(client):
    yield
    try:
        resp = client.get("/containers", params={"user_id": TEST_USER})
        if resp.status_code == 200:
            for c in resp.json():
                client.delete(f"/containers/{c['name']}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Test 1: Callback on container exit
# ---------------------------------------------------------------------------

class TestCallbackOnExit:
    """Create a short-lived container, verify callback fires with correct payload."""

    def test_callback_delivered(self, client, callback, callback_url):
        # Create container that exits immediately
        resp = client.post("/containers", json={
            "profile": "worker",
            "user_id": TEST_USER,
            "callback_url": callback_url,
            "config": {
                "command": ["echo", "hello"],
                "image": "alpine:latest",
            },
            "metadata": {"test": "g5-exit"},
        })
        assert resp.status_code == 201, f"Create failed: {resp.text}"
        data = resp.json()
        name = data["name"]
        assert data["status"] == "running"

        # Wait for callback
        payloads = callback.wait_for(count=1, timeout=15)
        assert len(payloads) >= 1, f"No callback received within 15s (got {len(payloads)})"

        cb = payloads[0]
        assert cb["name"] == name
        assert cb["exit_code"] == 0
        assert cb["status"] == "stopped"
        assert cb["profile"] == "worker"
        assert cb["metadata"] == {"test": "g5-exit"}
        assert "container_id" in cb


# ---------------------------------------------------------------------------
# Test 2: Full container lifecycle with callback
# ---------------------------------------------------------------------------

class TestLifecycleWithCallback:
    """Long-running container: create, list, touch, stop — callback on stop."""

    _name: str = ""

    def test_01_create_long_running(self, client, callback, callback_url):
        resp = client.post("/containers", json={
            "profile": "worker",
            "user_id": TEST_USER,
            "callback_url": callback_url,
            "config": {
                "command": ["sleep", "300"],
                "image": "alpine:latest",
            },
            "metadata": {"test": "g5-lifecycle"},
        })
        assert resp.status_code == 201, f"Create failed: {resp.text}"
        data = resp.json()
        TestLifecycleWithCallback._name = data["name"]
        assert data["status"] == "running"
        assert data["profile"] == "worker"

    def test_02_list_shows_container(self, client):
        name = TestLifecycleWithCallback._name
        assert name, "Container not created"
        resp = client.get("/containers", params={"user_id": TEST_USER})
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert name in names

    def test_03_touch_heartbeat(self, client):
        name = TestLifecycleWithCallback._name
        resp = client.post(f"/containers/{name}/touch")
        assert resp.status_code == 200

    def test_04_stop_and_callback(self, client, callback):
        name = TestLifecycleWithCallback._name
        before = len(callback.received)

        resp = client.delete(f"/containers/{name}")
        assert resp.status_code == 200

        # The event listener fires the callback on container die
        payloads = callback.wait_for(count=before + 1, timeout=15)
        # Find the callback for this container
        matching = [p for p in payloads if p.get("name") == name]
        assert len(matching) >= 1, f"No callback for {name} (total received: {len(payloads)})"
        cb = matching[0]
        assert cb["profile"] == "worker"
        assert cb["metadata"] == {"test": "g5-lifecycle"}
        assert "exit_code" in cb

    def test_05_container_stopped(self, client):
        name = TestLifecycleWithCallback._name
        resp = client.get(f"/containers/{name}")
        if resp.status_code == 200:
            assert resp.json().get("status") in ("stopped", "failed", "exited")
        else:
            assert resp.status_code == 404
