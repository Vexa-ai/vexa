"""``/ws`` and HTTP share one ban store (#1324).

End to end through the real ``create_app`` + ``apply_guard`` and Starlette's ASGI WebSocket
handshake, configured from env the way a deploy is. A ban set by either path is refused by the
other: an IP banned on HTTP cannot open ``/ws``, and an IP the ``/ws`` guard auto-bans gets
``403`` on HTTP. On the hand-rolled guard this replaced, ``/ws`` kept its own in-memory ban
dict, so neither direction held.

Self-contained (own fixtures, env only) so the same file runs against the pre-#1324 base.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from guard import ip_ban_manager
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import FakeAuthorizer, FakeDownstream, FakeRedis
from gateway.edge_guard import apply_guard

_PROXY = "127.0.0.1"


def _reset_guard_state() -> None:
    ip_ban_manager.redis_handler = None
    asyncio.run(ip_ban_manager.reset())
    from guard_core.handlers import ratelimit_handler

    for name in ("_by_ip_request_timestamps", "_by_ip_autoban_counts"):
        store = getattr(ratelimit_handler, name, None)
        if store is not None:
            store.clear()


@pytest.fixture(autouse=True)
def _guard_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GUARD_ENABLED", "true")
    monkeypatch.setenv("GUARD_WS_ENABLED", "true")
    monkeypatch.setenv("GUARD_ENABLE_REDIS", "false")
    monkeypatch.setenv("GUARD_TRUSTED_PROXIES", _PROXY)
    monkeypatch.setenv("GUARD_RATE_LIMIT_RPM", "1000")
    _reset_guard_state()
    yield
    _reset_guard_state()


def _client() -> TestClient:
    from gateway import create_app

    app: FastAPI = create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis())
    apply_guard(app)
    return TestClient(app, client=(_PROXY, 12345))


def _ws_refused(client: TestClient, ip: str) -> bool:
    """True when the guard refuses the upgrade pre-accept (1008); False when the connect
    gets through to the gateway's own auth (no key: ``missing_api_key``)."""
    try:
        with client.websocket_connect("/ws", headers={"X-Forwarded-For": ip}) as ws:
            assert ws.receive_json() == {"type": "error", "error": "missing_api_key"}
            return False
    except WebSocketDisconnect as exc:
        assert exc.code == 1008
        return True


def test_ip_banned_in_the_shared_store_is_refused_on_http_and_ws() -> None:
    client = _client()
    banned, clean = "10.0.88.1", "10.0.88.2"
    asyncio.run(ip_ban_manager.ban_ip(banned, 3600, "test"))

    assert client.get("/bots", headers={"X-Forwarded-For": banned}).status_code == 403
    assert _ws_refused(client, banned)
    assert not _ws_refused(client, clean)


def test_http_auto_ban_refuses_ws(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARD_RATE_LIMIT_RPM", "2")
    monkeypatch.setenv("GUARD_AUTO_BAN_RATE_LIMIT", "true")
    monkeypatch.setenv("GUARD_AUTO_BAN_THRESHOLD", "1")
    client = _client()
    ip = "10.0.88.3"

    statuses = [
        client.get("/bots", headers={"X-Forwarded-For": ip}).status_code
        for _ in range(4)
    ]
    assert statuses[-1] == 403, statuses  # banned on HTTP
    assert _ws_refused(client, ip)  # /ws has its own budget, so only the ban refuses it
    assert not _ws_refused(client, "10.0.88.4")


def test_ws_auto_ban_refuses_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARD_RATE_LIMIT_RPM", "2")
    monkeypatch.setenv("GUARD_AUTO_BAN_RATE_LIMIT", "true")
    monkeypatch.setenv("GUARD_AUTO_BAN_THRESHOLD", "1")
    client = _client()
    ip = "10.0.88.5"

    results = [_ws_refused(client, ip) for _ in range(3)]
    assert results == [False, False, True], results  # third connect over the /ws budget
    # HTTP has its own budget and this IP has spent none of it: only the ban refuses it.
    assert client.get("/bots", headers={"X-Forwarded-For": ip}).status_code == 403
    assert client.get("/bots", headers={"X-Forwarded-For": "10.0.88.6"}).status_code == 401


def test_rate_limit_auto_ban_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers behind one IP (a proxy with no GUARD_TRUSTED_PROXIES, the terminal, MCP) would
    share an auto-ban, so it stays opt-in, as in guard-core."""
    from gateway.edge_guard import build_guard_config

    monkeypatch.delenv("GUARD_AUTO_BAN_RATE_LIMIT", raising=False)
    assert build_guard_config().enable_rate_limit_auto_ban is False
