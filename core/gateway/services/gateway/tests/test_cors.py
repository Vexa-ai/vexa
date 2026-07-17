"""CORS for browser clients (0.10 parity) — CORS_ORIGINS env drives an allowlist.

The hosted dashboard is a browser app on its own origin (dashboard.<domain>) calling the
gateway's origin (api.<domain>): without CORS headers every call fails at the browser. The
0.10 api-gateway honored CORS_ORIGINS; the 0.12 gateway must too. Unset ⇒ no CORS headers
(server-to-server callers unaffected).
"""
from fastapi.testclient import TestClient

from gateway import create_app
from conftest import FakeAuthorizer, FakeDownstream, FakeRedis


def _app():
    return create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis())


def test_cors_preflight_allows_configured_origin(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://dashboard.example.com,https://app.example.com")
    client = TestClient(_app())
    r = client.options("/bots", headers={
        "Origin": "https://dashboard.example.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-api-key,content-type",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://dashboard.example.com"
    allowed_headers = r.headers.get("access-control-allow-headers", "").lower()
    assert "x-api-key" in allowed_headers or allowed_headers == "*"


def test_cors_rejects_unlisted_origin(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://dashboard.example.com")
    client = TestClient(_app())
    r = client.options("/bots", headers={
        "Origin": "https://evil.example.com",
        "Access-Control-Request-Method": "POST",
    })
    assert "access-control-allow-origin" not in r.headers


def test_no_cors_headers_when_unset(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    client = TestClient(_app())
    r = client.get("/health", headers={"Origin": "https://dashboard.example.com"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers
