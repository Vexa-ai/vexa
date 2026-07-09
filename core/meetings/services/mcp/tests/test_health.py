"""gate:health — the MCP service exposes a conforming liveness /health.

A pure liveness probe (process-up): no auth (mirrors the compose healthcheck), no gateway
hop. 200 + {status:"ok", service:"mcp"} means the service process is up. gate:health
discovers this package (it builds a FastAPI app) and runs this eval.
"""
from fastapi.testclient import TestClient


def test_health_ok(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "mcp"


def test_health_needs_no_api_key(client: TestClient, gateway):
    """Health must be reachable WITHOUT a credential — and must not hop to the gateway."""
    assert client.get("/health").status_code == 200
    assert gateway.requests == []
