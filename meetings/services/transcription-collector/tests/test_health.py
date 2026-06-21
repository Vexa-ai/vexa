"""gate:health — the transcription-collector exposes a conforming liveness /health.

The collector's liveness probe: no auth (mirrors a real load-balancer health check), no store
call. 200 + {status:"ok", service:"transcription-collector"} = the process is up.
"""
from fastapi.testclient import TestClient

from transcription_collector import create_app
from transcription_collector.fakes import InMemoryTranscriptStore


def _app():
    return create_app(InMemoryTranscriptStore(), redis=None)


def test_health_ok():
    client = TestClient(_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "transcription-collector"


def test_health_needs_no_user_identity():
    """Health must be reachable WITHOUT an x-user-id — it is not a client route."""
    client = TestClient(_app())
    assert client.get("/health").status_code == 200
