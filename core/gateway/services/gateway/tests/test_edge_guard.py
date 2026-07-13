"""Tests for the fastapi-guard integration at the v0.12 gateway edge (``edge_guard.py``).

Two layers:

* ``TestGuardWiring`` uses a real ``create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis())``
  with ``apply_guard(app)`` and conftest's safe env (``GUARD_ENABLE_REDIS=false``,
  ``GUARD_RATE_LIMIT_RPM=0``, ``GUARD_ENABLED=true`` — installed but inert). Proves guard is
  installed with safe, non-blocking defaults and does not regress the gateway.
* ``TestGuardBehavior`` builds isolated FastAPI apps with guard configured to actually enforce
  — per-IP rate limiting (in-memory, no Redis), X-Forwarded-For resolution behind a trusted
  proxy, and IP blacklisting. These prove the feature behaves, not just that it is wired.
* ``TestWsGuard`` exercises the optional ``GUARD_WS_ENABLED`` hook in ``run_multiplex`` — a
  blacklisted IP is denied at connect; a clean IP passes through to the auth layer.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx
import pytest
from fastapi import FastAPI
from guard import SecurityConfig
from httpx import ASGITransport
from starlette.websockets import WebSocketDisconnect

from conftest import FakeAuthorizer, FakeDownstream, FakeRedis, VALID_KEY
from gateway.app import run_multiplex
from gateway.edge_guard import apply_guard, build_guard_config, reset_ws_guard


def _guard_middleware(app: FastAPI) -> Any:
    """Return the SecurityMiddleware entry on ``app`` if present, else None."""
    for mw in app.user_middleware:
        if getattr(mw.cls, "__name__", "") == "SecurityMiddleware":
            return mw
    return None


def _enforcing_config(**overrides: Any) -> SecurityConfig:
    """A guard config that enforces in-memory (no Redis) with extras off.

    Rate limiting is on and keyed in-process; penetration detection, CORS, security headers,
    and fail-secure are off so nothing but the behavior under test can produce a non-200.
    ``exclude_paths`` is empty so ``/`` is gated.
    """
    base: dict[str, Any] = {
        "enable_redis": False,
        "redis_url": None,
        "enable_rate_limiting": True,
        "rate_limit": 3,
        "rate_limit_window": 60,
        "enable_ip_banning": False,
        "enable_penetration_detection": False,
        "enable_cors": False,
        "security_headers": {"enabled": False},
        "fail_secure": False,
        "lazy_init": True,
        "exclude_paths": [],
    }
    base.update(overrides)
    return SecurityConfig(**base)


async def _root_handler() -> dict[str, str]:
    """Trivial route body for the isolated behavioral apps."""
    return {"ok": "true"}


def _make_app(config: SecurityConfig) -> FastAPI:
    """A minimal FastAPI app with guard applied under ``config``."""
    app = FastAPI()
    app.add_api_route("/", _root_handler, methods=["GET"])
    apply_guard(app, config=config)
    return app


class TestGuardWiring:
    """Guard is installed on the real gateway with safe, non-blocking defaults."""

    def test_middleware_installed(self) -> None:
        """SecurityMiddleware is present after apply_guard (GUARD_ENABLED=true)."""
        app = create_app_with_guard()
        assert _guard_middleware(app) is not None

    def test_config_safe_defaults(self) -> None:
        """The hard-coded safety knobs keep guard from breaking the gateway or
        duplicating a future CORS / security-headers layer."""
        cfg = build_guard_config()
        # WAF body inspection is deferred (would false-positive on user text).
        assert cfg.enable_penetration_detection is False
        # A guard check bug must fail open, not 500 the public ingress.
        assert cfg.fail_secure is False
        # CORS + security-headers OFF (moot on 0.12, kept so a future layer can't double up).
        assert cfg.enable_cors is False
        assert cfg.security_headers is not None
        assert cfg.security_headers["enabled"] is False
        # Redis keys are namespaced away from Vexa's own (ratelimit:, gateway:).
        assert cfg.redis_prefix.startswith("vexa:")
        # /health is excluded so health monitors never trip the guard.
        assert "/health" in cfg.exclude_paths

    @pytest.mark.asyncio
    async def test_smoke_health_with_guard_active(self) -> None:
        """A public request still succeeds with guard in the stack and no Redis
        available — guard must not crash or block the liveness route."""
        app = create_app_with_guard()
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def create_app_with_guard() -> FastAPI:
    """A real ``create_app`` with fakes + ``apply_guard`` (mirrors build_production_app)."""
    app = create_app()
    apply_guard(app)
    return app


def create_app() -> FastAPI:
    """Build the gateway app with the conftest fakes (no network)."""
    from gateway import create_app as _create_app

    return _create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis())


class TestGuardBehavior:
    """Guard actually enforces per-IP limits, XFF resolution, and IP blocking."""

    @pytest.mark.asyncio
    async def test_per_ip_rate_limit_returns_429(self) -> None:
        """The Nth+1 request from one IP is rejected with 429; the first N pass."""
        app = _make_app(_enforcing_config(rate_limit=3))
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            for _ in range(3):
                resp = await ac.get("/")
                assert resp.status_code == 200
            resp = await ac.get("/")
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_xff_resolves_to_distinct_ip_buckets(self) -> None:
        """Behind a trusted proxy, guard keys on X-Forwarded-For, so two clients
        sharing the proxy IP get separate rate-limit buckets."""
        app = _make_app(_enforcing_config(rate_limit=2, trusted_proxies=["127.0.0.1"]))
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # IP 10.0.0.1 exhausts its 2-request bucket...
            for _ in range(2):
                assert (
                    await ac.get("/", headers={"X-Forwarded-For": "10.0.0.1"})
                ).status_code == 200
            assert (
                await ac.get("/", headers={"X-Forwarded-For": "10.0.0.1"})
            ).status_code == 429
            # ...while 10.0.0.2 on the same proxy is unaffected.
            assert (
                await ac.get("/", headers={"X-Forwarded-For": "10.0.0.2"})
            ).status_code == 200

    @pytest.mark.asyncio
    async def test_blacklisted_ip_returns_403(self) -> None:
        """A request whose resolved client IP is on the blacklist is blocked."""
        app = _make_app(
            _enforcing_config(
                rate_limit=1000,
                trusted_proxies=["127.0.0.1"],
                blacklist=["10.0.0.9"],
            )
        )
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/", headers={"X-Forwarded-For": "10.0.0.9"})
        assert resp.status_code == 403


# ── WS guard hook ──────────────────────────────────────────────────────────────


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class FakeWS:
    """Minimal WebSocket for the WS-guard tests: has ``.client`` (for IP resolution)
    and ``.headers`` (for X-Forwarded-For). No inbound frames — a denied connect never
    reaches the frame loop, and a clean IP test asserts it passes the guard (then hits
    the auth layer, which closes 4401 on a missing key)."""

    def __init__(self, *, client_host: str = "127.0.0.1", xff: Optional[str] = None,
                 api_key: Optional[str] = None) -> None:
        self.client = _FakeClient(client_host)
        headers: dict[str, str] = {}
        if xff:
            headers["x-forwarded-for"] = xff
        if api_key:
            headers["x-api-key"] = api_key
        self.headers = headers
        self.query_params: dict[str, str] = {}
        self.sent: list[dict] = []
        self.close_code: Optional[int] = None

    async def accept(self) -> None:
        pass

    async def send_text(self, data: str) -> None:
        try:
            self.sent.append(json.loads(data))
        except Exception:
            self.sent.append({"__raw__": data})

    async def receive_text(self) -> str:
        # A denied connect returns before the loop; a clean-IP-no-key connect also returns
        # before the loop (missing_api_key). Neither reaches receive_text.
        raise WebSocketDisconnect(code=1000)

    async def close(self, code: int = 1000) -> None:
        self.close_code = code


class TestWsGuard:
    """The optional GUARD_WS_ENABLED hook denies over-limit/banned IPs at WS connect."""

    @pytest.mark.asyncio
    async def test_blacklisted_ip_denied_at_connect(self, monkeypatch) -> None:
        """A WS connect from a blacklisted IP is denied with a 4401 close + ip_blocked error."""
        monkeypatch.setenv("GUARD_WS_ENABLED", "true")
        reset_ws_guard(_enforcing_config(
            rate_limit=1000, trusted_proxies=["127.0.0.1"], blacklist=["10.0.0.9"],
        ))
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.9", api_key=VALID_KEY)
        await run_multiplex(ws, FakeAuthorizer(valid_key=VALID_KEY), FakeRedis())
        assert ws.close_code == 4401
        assert ws.sent and ws.sent[0].get("error") == "ip_blocked"

    @pytest.mark.asyncio
    async def test_clean_ip_passes_guard_to_auth(self, monkeypatch) -> None:
        """A WS connect from a clean IP passes the guard and reaches the auth layer
        (no api_key → missing_api_key + 4401), proving the guard did not block it."""
        monkeypatch.setenv("GUARD_WS_ENABLED", "true")
        reset_ws_guard(_enforcing_config(rate_limit=1000))
        ws = FakeWS(client_host="127.0.0.1", api_key=None)
        await run_multiplex(ws, FakeAuthorizer(valid_key=VALID_KEY), FakeRedis())
        assert ws.close_code == 4401
        assert ws.sent and ws.sent[0].get("error") == "missing_api_key"

    @pytest.mark.asyncio
    async def test_over_limit_ip_denied_at_connect(self, monkeypatch) -> None:
        """After exhausting its rate-limit bucket, a WS connect from the same IP is denied."""
        monkeypatch.setenv("GUARD_WS_ENABLED", "true")
        cfg = _enforcing_config(rate_limit=2, trusted_proxies=["127.0.0.1"])
        reset_ws_guard(cfg)
        # Two connects pass the guard (reach the auth layer → missing_api_key + 4401)...
        for _ in range(2):
            ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.5", api_key=None)
            await run_multiplex(ws, FakeAuthorizer(valid_key=VALID_KEY), FakeRedis())
            assert ws.sent and ws.sent[0].get("error") == "missing_api_key"
        # ...the third is denied by the guard (ip_blocked, not missing_api_key).
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.5", api_key=VALID_KEY)
        await run_multiplex(ws, FakeAuthorizer(valid_key=VALID_KEY), FakeRedis())
        assert ws.close_code == 4401
        assert ws.sent and ws.sent[0].get("error") == "ip_blocked"