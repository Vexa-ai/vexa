"""gate:health — agent-api answers a conforming liveness /health.

agent-api is no longer just a runtime worker carve: it ships a real FastAPI front door
(`create_app` → /invocations · /api/chat · /api/sessions · /health), so it is a standing HTTP
service and must expose a liveness probe. 200 {status:"ok", service} when the dispatcher (its one
hard dependency) is wired; 503 {status:"degraded"} when it is not (P18 — absence is a reported state).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from control_plane.api import create_app
from shared.config import load_settings
from control_plane.dispatch import Dispatcher


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "fake-token"


def test_health_ok_when_dispatcher_wired():
    app = create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()))
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "agent-api"
    assert body["checks"]["dispatcher"] is True


def test_health_503_when_dispatcher_absent():
    # No dispatcher → the service cannot do its one job; liveness must report degraded, not lie.
    app = create_app(None)  # type: ignore[arg-type]
    r = TestClient(app).get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["dispatcher"] is False
