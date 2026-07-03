"""Health + info contract — the seam the nginx LB and the compose healthcheck depend on."""
from __future__ import annotations


def test_health_503_when_model_unloaded(client):
    # At import the global model is None (no startup ran) → LB must see the worker as down.
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unhealthy"
    assert "device" in body and "model" in body


def test_health_200_when_model_loaded(client, loaded):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_root_reports_service_info(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "Vexa Transcription Service"
    assert body["endpoints"]["transcribe"] == "/v1/audio/transcriptions"
    assert body["endpoints"]["health"] == "/health"
