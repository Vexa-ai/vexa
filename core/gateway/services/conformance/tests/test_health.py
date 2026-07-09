"""gate:health — the gateway exposes a conforming liveness /health.

The edge's liveness probe: no auth (mirrors a real load-balancer health check), no downstream
call. 200 + {status:"ok", service:"gateway"} means the gateway process is up.
"""
from fastapi.testclient import TestClient

from gateway_conformance.gateway_app import build_gateway


def test_health_ok():
    client = TestClient(build_gateway())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "gateway"


def test_health_needs_no_api_key():
    """Health must be reachable WITHOUT an x-api-key — it is not a client route."""
    client = TestClient(build_gateway())
    assert client.get("/health").status_code == 200
