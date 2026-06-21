"""gate:health — admin-api exposes a conforming liveness /health.

A pure liveness probe (process-up): no DB dependency, so it returns 200 without a live
Postgres. Readiness (DB reachable) is a separate concern covered by the stack evals.
"""
from fastapi.testclient import TestClient

from admin_api.app.main import create_app


def test_health_ok():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "admin-api"
