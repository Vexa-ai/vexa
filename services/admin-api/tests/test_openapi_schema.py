"""Regression test for issue #80 — OpenAPI security scheme names must be distinct.

FastAPI collapses APIKeyHeader instances that share the same scheme_name into one
OpenAPI security scheme. Without explicit scheme_name values, Swagger UI shows the
wrong header (X-API-Key) in curl examples for admin endpoints.
"""

import os

os.environ.setdefault("ADMIN_API_TOKEN", "test-admin-token")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_openapi_security_schemes_are_distinct():
    """AdminApiKey and UserApiKey must be separate schemes so Swagger shows the right header."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    schemes = resp.json()["components"]["securitySchemes"]
    assert "AdminApiKey" in schemes, "AdminApiKey scheme missing — admin curl examples will show wrong header"
    assert "UserApiKey" in schemes, "UserApiKey scheme missing — user curl examples will show wrong header"
    assert schemes["AdminApiKey"]["name"] == "X-Admin-API-Key"
    assert schemes["UserApiKey"]["name"] == "X-API-Key"
