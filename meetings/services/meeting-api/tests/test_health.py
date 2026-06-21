"""gate:health — meeting-api exposes a conforming liveness /health.

The receiver's health probe reports the process is up and how many meeting records the in-memory
store holds. Liveness only (no external dependency), so it is green without docker.
"""
from fastapi.testclient import TestClient

from meeting_api.lifecycle.receiver import create_app


def test_health_ok():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # the receiver reports its store depth alongside status
    assert "records" in body
