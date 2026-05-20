from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.security_headers import SecurityHeadersMiddleware


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/b/{token}/vnc/vnc.html")
    async def vnc_page(token: str):
        return {"token": token}

    @app.get("/meetings/{meeting_id}")
    async def meeting(meeting_id: int):
        return {"id": meeting_id}

    return TestClient(app, base_url="http://172.238.172.98:8056")


def test_vnc_frame_ancestors_allows_same_host_dashboard_port() -> None:
    response = _client().get(
        "/b/37/vnc/vnc.html",
        headers={"referer": "http://172.238.172.98:3000/meetings/37"},
    )

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "frame-ancestors 'self' http://localhost:* https://localhost:* http://172.238.172.98:3000"
    )
    assert "x-frame-options" not in response.headers


def test_vnc_frame_ancestors_rejects_cross_host_referer() -> None:
    response = _client().get(
        "/b/37/vnc/vnc.html",
        headers={"referer": "http://example.com/meetings/37"},
    )

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "frame-ancestors 'self' http://localhost:* https://localhost:*"
    )


def test_non_vnc_routes_keep_frame_deny() -> None:
    response = _client().get("/meetings/37")

    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
