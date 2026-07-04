"""Tests for the fastapi-guard integration at the api-gateway.

Two layers:

* ``TestGuardWiring`` uses the real gateway app imported from ``main``. Conftest
  runs it with ``GUARD_ENABLE_REDIS=false`` and ``GUARD_RATE_LIMIT_RPM=0`` so the
  existing proxy suite stays deterministic and never needs Redis — this proves
  guard is installed with safe, non-blocking defaults and does not regress the
  gateway.
* ``TestGuardBehavior`` builds isolated FastAPI apps with guard configured to
  actually enforce — per-IP rate limiting (in-memory, no Redis),
  X-Forwarded-For resolution behind a trusted proxy, and IP blacklisting. These
  prove the feature behaves, not just that it is wired.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from guard import SecurityConfig
from httpx import ASGITransport
from starlette.middleware import Middleware

from guard_config import apply_guard, build_guard_config
from main import app as gateway_app


def _guard_middleware(app: FastAPI) -> Middleware | None:
    """Return the SecurityMiddleware entry on ``app`` if present, else None."""
    for mw in app.user_middleware:
        if getattr(mw.cls, "__name__", "") == "SecurityMiddleware":
            return mw
    return None


def _enforcing_config(**overrides: Any) -> SecurityConfig:
    """A guard config that enforces in-memory (no Redis) with extras off.

    Rate limiting is on and keyed in-process; penetration detection, CORS,
    security headers, and fail-secure are off so nothing but the behavior under
    test can produce a non-200. ``exclude_paths`` is empty so ``/`` is gated.
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
        """SecurityMiddleware is present on the gateway (GUARD_ENABLED=true)."""
        assert _guard_middleware(gateway_app) is not None

    def test_config_safe_defaults(self) -> None:
        """The hard-coded safety knobs keep guard from breaking the gateway or
        duplicating Vexa's existing CORS / security-headers middleware."""
        cfg = build_guard_config()
        # WAF body inspection is deferred (would false-positive on user text).
        assert cfg.enable_penetration_detection is False
        # A guard check bug must fail open, not 500 the public ingress.
        assert cfg.fail_secure is False
        # Vexa keeps its own CORSMiddleware + SecurityHeadersMiddleware.
        assert cfg.enable_cors is False
        assert cfg.security_headers is not None
        assert cfg.security_headers["enabled"] is False
        # Redis keys are namespaced away from Vexa's own (ratelimit:, gateway:).
        assert cfg.redis_prefix.startswith("vexa:")

    @pytest.mark.asyncio
    async def test_smoke_root_with_guard_active(self) -> None:
        """A public request still succeeds with guard in the stack and no Redis
        available — guard must not crash or block the root route."""
        async with httpx.AsyncClient(
            transport=ASGITransport(app=gateway_app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/")
        assert resp.status_code == 200


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
