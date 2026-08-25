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
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, Optional

import httpx
import pytest
from fastapi import FastAPI
from guard import SecurityConfig, ip_ban_manager
from httpx import ASGITransport
from starlette.datastructures import Headers
from starlette.websockets import WebSocketDisconnect

from conftest import FakeAuthorizer, FakeDownstream, FakeRedis, VALID_KEY
from gateway.app import run_multiplex
from gateway.config_preflight import ConfigError
from gateway.edge_guard import (
    _GUARD_EXCLUDE_PATHS,
    apply_guard,
    build_guard_config,
    reset_ws_guard,
)
from gateway.ratelimit import PerUserRateLimiter


@pytest.fixture(autouse=True)
async def _reset_guard_process_state() -> AsyncIterator[None]:
    """Reset guard-core's process-global WS state between tests.

    Phase 2 moved the WS rate limit / auto-ban onto guard-core library primitives, which
    keep their own module-level stores (NOT the per-instance ``_WsGuard`` dict the old
    hand-rolled code used, which ``reset_ws_guard`` rebuilt fresh each test). Three
    singletons survive across tests otherwise:

    * ``ip_ban_manager`` (``guard_core.handlers.ipban_handler``) - the ban store the WS
      check and HTTP pipeline share. Its own ``reset()`` clears state in place, which
      works no matter which import path a caller holds a reference through (unlike
      swapping in a brand-new instance). Forcing ``redis_handler = None`` keeps every test
      in-memory / hermetic (conftest.py sets ``GUARD_ENABLE_REDIS=false`` for the suite).
    * ``_by_ip_request_timestamps`` and ``_by_ip_autoban_counts``
      (``guard_core.handlers.ratelimit_handler``) - the ``check_rate_limit_by_ip``
      primitive's in-memory sliding window and per-IP violation counter. Clearing them in
      place is the same idiom ``ip_ban_manager.reset()`` uses; without it, a 127.0.0.1
      bucket one test populated leaks into the next (the suite failed on
      ``test_untrusted_peer_xff_does_not_rotate_ws_budget`` because an earlier test left
      peer-IP hits there).
    """
    from guard_core.handlers import ratelimit_handler as _rl_handler

    ip_ban_manager.redis_handler = None
    await ip_ban_manager.reset()
    _rl_handler._by_ip_request_timestamps.clear()
    _rl_handler._by_ip_autoban_counts.clear()
    yield
    await ip_ban_manager.reset()
    _rl_handler._by_ip_request_timestamps.clear()
    _rl_handler._by_ip_autoban_counts.clear()


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

    def test_env_bool_accepts_shared_truthy_set(self, monkeypatch) -> None:
        """#567: ``_env_bool`` honors the shared truthy set (``1/true/yes/on``, case-
        insensitive) — not just literal ``true``. Pins ``GUARD_ENABLED=1`` etc. as ON and
        ``0/false/``/``no`` as OFF, so ``GUARD_ENABLED=1`` no longer silently disables."""
        from gateway.edge_guard import _env_bool

        truthy = ["1", "true", "TRUE", "True", "yes", "YES", "on", "ON"]
        falsy = ["0", "false", "FALSE", "", "no", "off", "random"]
        for val in truthy:
            monkeypatch.setenv("GUARD_ENABLED_TEST", val)
            assert _env_bool("GUARD_ENABLED_TEST", False) is True, (
                f"{val!r} should be truthy"
            )
        for val in falsy:
            monkeypatch.setenv("GUARD_ENABLED_TEST", val)
            assert _env_bool("GUARD_ENABLED_TEST", False) is False, (
                f"{val!r} should be falsy"
            )
        # Missing var → default (both directions).
        monkeypatch.delenv("GUARD_ENABLED_TEST", raising=False)
        assert _env_bool("GUARD_ENABLED_TEST", True) is True
        assert _env_bool("GUARD_ENABLED_TEST", False) is False


class TestGuardEnvValidation:
    """The IP-list fields (``whitelist`` / ``blacklist`` / ``trusted_proxies``) already raise on
    the guard-core version this repo pins (3.4.0); ``block_cloud_providers`` on that same
    version is a SILENT case-sensitive filter (a bad or lowercase entry is dropped, not
    rejected) that a later guard-core (>=3.12.0) turns into a raise. Either way, without a
    Vexa-side check a bad operator entry either silently vanishes or surfaces as a bare
    ``ValueError``/pydantic stack trace from deep inside ``SecurityConfig(...)`` construction.
    ``build_guard_config`` now pre-validates all four and raises :class:`ConfigError` naming the
    var, the bad entry, and (for cloud providers) the accepted spellings, before the library
    ever sees the value. See ``edge_guard.py`` for the field-by-field evidence."""

    def test_unrecognized_cloud_provider_raises_clear_error(self, monkeypatch) -> None:
        """A name that is not case-insensitively AWS/GCP/Azure raises, naming the var, the
        entry, and the exact accepted spellings - the natural 'aws,digitalocean' typo case."""
        monkeypatch.setenv("GUARD_BLOCK_CLOUD_PROVIDERS", "aws,digitalocean")
        with pytest.raises(
            ConfigError, match="GUARD_BLOCK_CLOUD_PROVIDERS"
        ) as exc_info:
            build_guard_config()
        message = str(exc_info.value)
        assert "digitalocean" in message
        assert "AWS, GCP, Azure" in message

    def test_cloud_provider_mixed_case_and_region_carveout_construct(
        self, monkeypatch
    ) -> None:
        """Lowercase/mixed-case provider names normalize to guard-core's canonical spelling
        (aws -> AWS); the ':!region' carve-out suffix survives untouched."""
        monkeypatch.setenv("GUARD_BLOCK_CLOUD_PROVIDERS", "aws,GCP,Azure:!us-east-1")
        cfg = build_guard_config()
        assert cfg.block_cloud_providers == {"AWS", "GCP", "Azure:!us-east-1"}

    def test_uppercase_region_carveout_raises_clear_error(self, monkeypatch) -> None:
        """guard-core's ``is_cloud_ip`` matches a carve-out region with a case-sensitive '==';
        real region strings are lowercase, so an uppercase region would silently never match.
        Vexa rejects it at boot instead, naming the casing rule."""
        monkeypatch.setenv("GUARD_BLOCK_CLOUD_PROVIDERS", "AWS:!US-EAST-1")
        with pytest.raises(
            ConfigError, match="GUARD_BLOCK_CLOUD_PROVIDERS"
        ) as exc_info:
            build_guard_config()
        message = str(exc_info.value)
        assert "US-EAST-1" in message
        assert "lowercase" in message

    def test_lowercase_region_carveout_constructs(self, monkeypatch) -> None:
        """A properly lowercase carve-out region normalizes only the provider half."""
        monkeypatch.setenv("GUARD_BLOCK_CLOUD_PROVIDERS", "aws:!us-east-1")
        cfg = build_guard_config()
        assert cfg.block_cloud_providers == {"AWS:!us-east-1"}

    def test_global_region_carveout_constructs(self, monkeypatch) -> None:
        """'GLOBAL' is the one synthetic, uppercase region guard-core (AWS) actually uses, so
        it is accepted as-is rather than rejected by the lowercase rule."""
        monkeypatch.setenv("GUARD_BLOCK_CLOUD_PROVIDERS", "AWS:!GLOBAL")
        cfg = build_guard_config()
        assert cfg.block_cloud_providers == {"AWS:!GLOBAL"}

    def test_empty_region_carveout_raises_clear_error(self, monkeypatch) -> None:
        """A ':!' suffix with nothing after it is not a valid carve-out."""
        monkeypatch.setenv("GUARD_BLOCK_CLOUD_PROVIDERS", "AWS:!")
        with pytest.raises(
            ConfigError, match="GUARD_BLOCK_CLOUD_PROVIDERS"
        ) as exc_info:
            build_guard_config()
        assert "empty region" in str(exc_info.value)

    def test_invalid_whitelist_entry_raises_clear_error(self, monkeypatch) -> None:
        monkeypatch.setenv("GUARD_IP_WHITELIST", "10.0.0.1,not-an-ip")
        with pytest.raises(ConfigError, match="GUARD_IP_WHITELIST") as exc_info:
            build_guard_config()
        assert "not-an-ip" in str(exc_info.value)

    def test_invalid_blacklist_entry_raises_clear_error(self, monkeypatch) -> None:
        """A CIDR prefix out of range (/99) is invalid and must not reach guard-core."""
        monkeypatch.setenv("GUARD_IP_BLACKLIST", "10.0.0.0/99")
        with pytest.raises(ConfigError, match="GUARD_IP_BLACKLIST") as exc_info:
            build_guard_config()
        assert "10.0.0.0/99" in str(exc_info.value)

    def test_invalid_trusted_proxies_entry_raises_clear_error(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("GUARD_TRUSTED_PROXIES", "999.999.999.999")
        with pytest.raises(ConfigError, match="GUARD_TRUSTED_PROXIES"):
            build_guard_config()

    def test_valid_ip_and_cidr_entries_construct(self, monkeypatch) -> None:
        """Good IP + CIDR entries across all three IP-list vars still construct a working
        config. Compared as ``list(...)`` since guard-core's own field type (list vs. tuple)
        has varied across versions - not this fix's concern, only that construction succeeds
        with the right values."""
        monkeypatch.setenv("GUARD_IP_WHITELIST", "10.0.0.1, 10.1.0.0/16")
        monkeypatch.setenv("GUARD_IP_BLACKLIST", "192.168.1.1")
        monkeypatch.setenv("GUARD_TRUSTED_PROXIES", "127.0.0.1")
        cfg = build_guard_config()
        assert list(cfg.whitelist) == ["10.0.0.1", "10.1.0.0/16"]
        assert list(cfg.blacklist) == ["192.168.1.1"]
        assert list(cfg.trusted_proxies) == ["127.0.0.1"]

    def test_unset_vars_still_default_to_empty(self, monkeypatch) -> None:
        """No GUARD_* filter vars set at all → the pre-validation is a no-op and guard-core's
        own defaults apply, same as before this change."""
        for var in (
            "GUARD_IP_WHITELIST",
            "GUARD_IP_BLACKLIST",
            "GUARD_TRUSTED_PROXIES",
            "GUARD_BLOCK_CLOUD_PROVIDERS",
        ):
            monkeypatch.delenv(var, raising=False)
        cfg = build_guard_config()
        assert cfg.whitelist is None
        assert not cfg.blacklist
        assert not cfg.trusted_proxies
        assert not cfg.block_cloud_providers


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

    @pytest.mark.asyncio
    async def test_real_exclude_paths_do_not_neuter_guard(self) -> None:
        """Regression: guard matches ``exclude_paths`` with ``url_path.startswith``
        (prefix, not exact), so a bare ``"/"`` in the list matches EVERY path and
        silently disables the whole guard layer — no rate limit, no IP ban, nothing.
        The real ``_GUARD_EXCLUDE_PATHS`` must not contain ``"/"``.

        Floods ``/`` with the REAL exclude list (not the ``exclude_paths=[]`` the
        other behavior tests use) + ``rate_limit=3`` and asserts a 429 on the 4th
        request: if ``"/"`` were present this would be a silent 200 bypass. Caught
        by live image testing — the unit suite used ``exclude_paths=[]`` and so
        missed that the shipped config neutered guard on every route. Unique XFF
        IP (10.0.0.99) avoids the process-wide RateLimitManager singleton bucket
        pollution the other behavior tests share."""
        assert "/" not in _GUARD_EXCLUDE_PATHS  # the load-bearing bug guard
        app = _make_app(
            _enforcing_config(
                rate_limit=3,
                trusted_proxies=["127.0.0.1"],
                exclude_paths=_GUARD_EXCLUDE_PATHS,
            )
        )
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            for _ in range(3):
                assert (
                    await ac.get("/", headers={"X-Forwarded-For": "10.0.0.99"})
                ).status_code == 200
            # 4th: over the per-IP budget -> guard 429. If "/" were in exclude_paths
            # this would be 200 (silent bypass) — the regression.
            assert (
                await ac.get("/", headers={"X-Forwarded-For": "10.0.0.99"})
            ).status_code == 429


# ── WS guard hook ──────────────────────────────────────────────────────────────


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class FakeWS:
    """Minimal WebSocket for the WS-guard tests: has ``.client`` (for IP resolution),
    ``.headers`` (for X-Forwarded-For) and ``.state`` (SWAP C: ``resolve_ws_client_ip``
    now routes through ``guard_core.utils.extract_client_ip``, which reads both - a real
    Starlette ``WebSocket`` always has them, sharing ``HTTPConnection`` with ``Request``).
    ``.headers`` is a real Starlette ``Headers`` (case-insensitive), matching production
    and what ``extract_client_ip`` expects (it looks up ``X-Forwarded-For`` by that exact
    casing). No inbound frames - a denied connect never reaches the frame loop, and a
    clean IP test asserts it passes the guard (then hits the auth layer, which closes
    4401 on a missing key)."""

    def __init__(
        self,
        *,
        client_host: str = "127.0.0.1",
        xff: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.client = _FakeClient(client_host)
        raw_headers: dict[str, str] = {}
        if xff:
            raw_headers["x-forwarded-for"] = xff
        if api_key:
            raw_headers["x-api-key"] = api_key
        self.headers = Headers(headers=raw_headers)
        self.state = SimpleNamespace()
        self.query_params: dict[str, str] = {}
        self.sent: list[dict] = []
        self.close_code: Optional[int] = None
        self.accepted: bool = (
            False  # True once accept() is called — proves pre-accept rejection
        )

    async def accept(self) -> None:
        self.accepted = True

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
        """A WS connect from a blacklisted IP is denied BEFORE the upgrade — close 4401
        pre-accept, so no WebSocket is opened (``accepted`` stays False) and no data frame
        is sent (Starlette turns a pre-accept ``websocket.close`` into an HTTP 403)."""
        monkeypatch.setenv("GUARD_WS_ENABLED", "true")
        reset_ws_guard(
            _enforcing_config(
                rate_limit=1000,
                trusted_proxies=["127.0.0.1"],
                blacklist=["10.0.0.9"],
            )
        )
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.9", api_key=VALID_KEY)
        await run_multiplex(ws, FakeAuthorizer(valid_key=VALID_KEY), FakeRedis())
        assert ws.close_code == 4401
        assert ws.accepted is False  # pre-accept rejection - no WebSocket upgrade
        assert not ws.sent  # no data frame before accept

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
        # ...the third is denied by the guard PRE-ACCEPT (4401, no upgrade, no data frame).
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.5", api_key=VALID_KEY)
        await run_multiplex(ws, FakeAuthorizer(valid_key=VALID_KEY), FakeRedis())
        assert ws.close_code == 4401
        assert ws.accepted is False  # pre-accept rejection - no WebSocket upgrade
        assert not ws.sent

    @pytest.mark.asyncio
    async def test_auto_ban_persists_past_window_rebans_without_reset(
        self, monkeypatch
    ) -> None:
        """Phase 2 library semantics (guard-core 3.13.0 ``check_rate_limit_by_ip``).

        An auto-ban PERSISTS past the rate-window reset (still within the ban duration):
        the ban STORE (``ip_ban_manager``) is what gates the connect, not the rate window,
        so a banned IP stays denied even when its rate bucket has drained.

        The violation counter the library keeps does NOT reset when it sets a ban (unlike
        the old hand-rolled ``_ban_counts`` dict, which zeroed on ban-set). It only stops
        counting while the IP is banned (the WS check short-circuits at ``is_ip_banned``
        before the primitive is ever called), then resumes from its old value. So after a
        ban EXPIRES the offender gets a fresh rate WINDOW (no hits recorded during the
        ban), but the first over-limit once that window exhausts RE-BANS immediately,
        rather than needing a full threshold again. Stricter on recidivism; this is
        guard-core's chosen semantics, not tuned here.

        A fake clock drives both the rate window (the primitive reads ``time.time()``)
        and the ban-expiry check (``ip_ban_manager.is_ip_banned`` reads ``time.time()``).
        Both go through the shared stdlib ``time`` module, so patching ``time.time`` on it
        redirects both. ``enable_rate_limit_auto_ban=True`` + ``enable_ip_banning=True`` +
        ``auto_ban_threshold=2`` so two over-limit events set the ban (the knob pair the
        library primitive requires; the old hand-roll auto-banned on ``enable_ip_banning``
        alone, with no separate knob).
        """
        import time as _time

        monkeypatch.setenv("GUARD_WS_ENABLED", "true")
        cfg = _enforcing_config(
            rate_limit=2,
            rate_limit_window=60,
            enable_ip_banning=True,
            enable_rate_limit_auto_ban=True,
            auto_ban_threshold=2,
            auto_ban_duration=3600,
            trusted_proxies=["127.0.0.1"],
        )
        reset_ws_guard(cfg)
        clock = {"t": 0.0}
        monkeypatch.setattr(_time, "time", lambda: clock["t"])

        auth = FakeAuthorizer(valid_key=VALID_KEY)
        redis = FakeRedis()

        # Two connects pass the guard (bucket for 10.0.0.7 has room).
        for _ in range(2):
            ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.7", api_key=None)
            await run_multiplex(ws, auth, redis)
            assert ws.sent and ws.sent[0].get("error") == "missing_api_key"
        # 3rd: over limit -> violation count 1 (< threshold 2) -> denied pre-accept, no ban.
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.7", api_key=VALID_KEY)
        await run_multiplex(ws, auth, redis)
        assert ws.close_code == 4401
        assert ws.accepted is False  # pre-accept rejection
        # 4th: over limit again -> count 2 (>= threshold) -> BAN set.
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.7", api_key=VALID_KEY)
        await run_multiplex(ws, auth, redis)
        assert ws.close_code == 4401
        assert ws.accepted is False

        # Ban PERSISTS past the rate window (61s) but within the ban (3600s) -> still banned.
        # The check short-circuits at is_ip_banned; the primitive (and its counter) is idle.
        clock["t"] = 61.0
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.7", api_key=VALID_KEY)
        await run_multiplex(ws, auth, redis)
        assert ws.close_code == 4401
        assert ws.accepted is False  # ban persists - still denied pre-accept

        # Past the ban (3601s) -> ban expired, fresh rate WINDOW (no hits recorded during
        # the ban, so the deque is empty). Two connects pass.
        clock["t"] = 3601.0
        for _ in range(2):
            ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.7", api_key=None)
            await run_multiplex(ws, auth, redis)
            assert ws.sent and ws.sent[0].get("error") == "missing_api_key"
        # First over-limit after expiry -> count resumes at 3 (>= threshold 2) -> RE-BAN
        # immediately. The old reset-on-set would have left the count at 0 and needed two
        # over-limits; the library does not reset, so one suffices.
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.7", api_key=VALID_KEY)
        await run_multiplex(ws, auth, redis)
        assert ws.close_code == 4401
        assert ws.accepted is False
        # Still inside the new ban window (3662s < 3601+3600) -> still banned, denied.
        clock["t"] = 3662.0
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.7", api_key=VALID_KEY)
        await run_multiplex(ws, auth, redis)
        assert ws.close_code == 4401
        assert ws.accepted is False

    @pytest.mark.asyncio
    async def test_untrusted_peer_xff_does_not_rotate_ws_budget(
        self, monkeypatch
    ) -> None:
        """A4 spoofed-XFF (WS): when the peer is NOT a trusted proxy, varying XFF values
        must NOT rotate the WS rate-limit budget — all connects key on the peer IP.
        Covers the D1 fix (untrusted peer's XFF is ignored)."""
        monkeypatch.setenv("GUARD_WS_ENABLED", "true")
        # 127.0.0.1 is NOT in trusted_proxies → XFF ignored, peer IP keys the bucket.
        reset_ws_guard(_enforcing_config(rate_limit=2, trusted_proxies=[]))
        auth = FakeAuthorizer(valid_key=VALID_KEY)
        redis = FakeRedis()
        # Two connects pass the guard (peer-IP bucket has room); each carries a DIFFERENT
        # XFF value to prove the header is not rotating the budget.
        for i in range(2):
            ws = FakeWS(client_host="127.0.0.1", xff=f"10.0.0.{i}", api_key=None)
            await run_multiplex(ws, auth, redis)
            assert ws.sent and ws.sent[0].get("error") == "missing_api_key"
        # 3rd connect with yet another XFF → still denied pre-accept (peer-IP bucket
        # exhausted; the XFF did not create a fresh bucket).
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.99", api_key=VALID_KEY)
        await run_multiplex(ws, auth, redis)
        assert ws.close_code == 4401
        assert ws.accepted is False  # pre-accept rejection

    @pytest.mark.asyncio
    async def test_cidr_ranges_match_on_ws_path(self, monkeypatch) -> None:
        """#565: WS guard matches GUARD_TRUSTED_PROXIES / whitelist / blacklist entries as
        CIDR ranges (not exact strings), mirroring the fastapi-guard HTTP path. A ``/8``
        trusted-proxy CIDR matches a contained peer; a ``/24`` blacklist CIDR matches a
        contained XFF IP and denies the connect; an out-of-range IP is NOT matched."""
        monkeypatch.setenv("GUARD_WS_ENABLED", "true")
        reset_ws_guard(
            _enforcing_config(
                rate_limit=1000,
                # 10.0.0.0/8 trusted-proxy CIDR — 127.0.0.1 is NOT in it, so a peer at
                # 127.0.0.1 is untrusted and its XFF is ignored. Instead we put a
                # 127.0.0.0/8 CIDR so 127.0.0.1 IS a trusted proxy and XFF is honored.
                trusted_proxies=["127.0.0.0/8"],
                # 10.0.0.0/24 blacklist CIDR — 10.0.0.50 is contained → denied; 10.0.1.5
                # is out of range → not matched.
                blacklist=["10.0.0.0/24"],
            )
        )
        auth = FakeAuthorizer(valid_key=VALID_KEY)
        redis = FakeRedis()
        # Contained in the /24 blacklist CIDR → denied pre-accept.
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.50", api_key=VALID_KEY)
        await run_multiplex(ws, auth, redis)
        assert ws.close_code == 4401
        assert ws.accepted is False
        # Out of the /24 range (10.0.1.5) → passes the guard, reaches auth (missing_api_key).
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.1.5", api_key=None)
        await run_multiplex(ws, auth, redis)
        assert ws.accepted is True
        assert ws.sent and ws.sent[0].get("error") == "missing_api_key"

    @pytest.mark.asyncio
    async def test_whitelist_cidr_bypasses_ws_guard(self, monkeypatch) -> None:
        """#565: a CIDR in GUARD_IP_WHITELIST bypasses the WS guard even when the IP is
        over the rate limit. ``10.0.0.0/16`` whitelist → 10.0.5.5 is contained and passes."""
        monkeypatch.setenv("GUARD_WS_ENABLED", "true")
        reset_ws_guard(
            _enforcing_config(
                rate_limit=1,
                trusted_proxies=["127.0.0.0/8"],
                whitelist=["10.0.0.0/16"],
            )
        )
        auth = FakeAuthorizer(valid_key=VALID_KEY)
        redis = FakeRedis()
        # 10.0.5.5 is whitelisted via the /16 CIDR — passes the guard every time (no
        # rate limit applied), reaches auth → missing_api_key.
        for _ in range(3):
            ws = FakeWS(client_host="127.0.0.1", xff="10.0.5.5", api_key=None)
            await run_multiplex(ws, auth, redis)
            assert ws.accepted is True
            assert ws.sent and ws.sent[0].get("error") == "missing_api_key"

    @pytest.mark.asyncio
    async def test_whitelist_exclusive_blocks_unlisted_ip(self, monkeypatch) -> None:
        """SWAP A / audit 5.4 (deliberate behavior change): guard-core's
        ``check_ip_access`` treats a non-empty whitelist as EXCLUSIVE - an IP that is
        NOT on it is blocked outright, the blacklist is never consulted. The OLD
        hand-rolled ``_ip_matches``-based check only used the whitelist as a bypass
        fast path: a listed IP passed immediately, but an UNLISTED IP simply fell
        through to the (here, empty) blacklist and rate limit and was ALLOWED. This
        pins the NEW, stricter, HTTP-matching behavior: with only ``10.0.0.0/24``
        whitelisted, an IP outside that range is denied even though it is not
        blacklisted and the rate limit has room."""
        monkeypatch.setenv("GUARD_WS_ENABLED", "true")
        reset_ws_guard(
            _enforcing_config(
                rate_limit=1000,
                trusted_proxies=["127.0.0.1"],
                whitelist=["10.0.0.0/24"],
            )
        )
        auth = FakeAuthorizer(valid_key=VALID_KEY)
        redis = FakeRedis()
        # 10.0.1.5 is outside the whitelisted /24 and not blacklisted -> denied
        # pre-accept under the new EXCLUSIVE semantics (the old code would have
        # allowed it through to the auth layer).
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.1.5", api_key=VALID_KEY)
        await run_multiplex(ws, auth, redis)
        assert ws.close_code == 4401
        assert ws.accepted is False  # pre-accept rejection - no WebSocket upgrade
        assert not ws.sent
        # A listed IP still passes, as before.
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.5", api_key=None)
        await run_multiplex(ws, auth, redis)
        assert ws.accepted is True
        assert ws.sent and ws.sent[0].get("error") == "missing_api_key"


# ── A5: both layers in one app ─────────────────────────────────────────────────


def _app_with_both_layers(rate_limiter: PerUserRateLimiter) -> FastAPI:
    """One app with BOTH the per-user rate_limiter and the per-IP guard installed,
    so the two 429 paths coexist (the A5 no-regression row)."""
    from gateway import create_app as _create_app

    app = _create_app(
        FakeAuthorizer(), FakeDownstream(), FakeRedis(), rate_limiter=rate_limiter
    )
    apply_guard(
        app,
        config=_enforcing_config(rate_limit=4, trusted_proxies=["127.0.0.1"]),
    )
    return app


class TestBothLayers:
    """A5: per-user rate_limiter and per-IP guard coexist — distinct 429 paths."""

    @pytest.mark.asyncio
    async def test_valid_key_429_from_rate_limiter_and_keyless_429_from_guard(
        self,
    ) -> None:
        """A valid-key burst → per-user 429 (rate_limiter, ``Retry-After: 1``); a keyless
        flood from one IP → per-IP 429 (guard, no ``Retry-After``). Neither shadows the other.

        Uses XFF IPs unique to this test (10.0.0.50/10.0.0.51): guard's
        ``RateLimitManager`` is a process-wide singleton, so the in-memory
        ``request_timestamps`` accumulate across tests — distinct IPs avoid
        cross-test bucket pollution (the existing tests each use their own IPs)."""
        # Per-user bucket: capacity 2, no refill → 3rd valid-key request 429s.
        limiter = PerUserRateLimiter(capacity=2, refill_per_sec=0)
        app = _app_with_both_layers(limiter)
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # Per-user path: two valid-key requests pass (guard's 10.0.0.50 bucket has
            # 2 < 4 so guard lets them through), the 3rd hits the per-user 429.
            for _ in range(2):
                resp = await ac.get(
                    "/bots",
                    headers={"x-api-key": VALID_KEY, "X-Forwarded-For": "10.0.0.50"},
                )
                assert resp.status_code == 200
            resp = await ac.get(
                "/bots",
                headers={"x-api-key": VALID_KEY, "X-Forwarded-For": "10.0.0.50"},
            )
            assert resp.status_code == 429
            # Retry-After: 1 is the per-user limiter's signature (app.py:154); guard's 429 has none.
            assert resp.headers.get("retry-after") == "1"

            # Per-IP path: keyless flood from a distinct IP → guard 429 (no Retry-After).
            for _ in range(4):
                resp = await ac.get("/bots", headers={"X-Forwarded-For": "10.0.0.51"})
                assert (
                    resp.status_code == 401
                )  # guard passes, auth rejects (missing key)
            resp = await ac.get("/bots", headers={"X-Forwarded-For": "10.0.0.51"})
            assert resp.status_code == 429  # guard per-IP 429
            assert (
                resp.headers.get("retry-after") is None
            )  # guard's 429, not the limiter's


class TestBannedButWhitelisted:
    async def test_ban_check_precedes_whitelist_bypass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A banned IP that is ALSO whitelisted stays blocked on the WS path.

        Pins the deliberate ordering change of the library consolidation: the ban check runs
        unconditionally BEFORE the whitelist decision, mirroring guard-core's IpSecurityCheck
        pipeline. The pre-consolidation hand-rolled guard did the opposite (whitelist bypassed
        everything, including bans); reordering the two checks back would let a banned IP
        re-enter through the whitelist, and this test fails if that regresses.
        """
        from guard import ip_ban_manager

        monkeypatch.setenv("GUARD_WS_ENABLED", "true")
        cfg = _enforcing_config(
            enable_ip_banning=True,
            whitelist=["10.0.0.7"],
            trusted_proxies=["127.0.0.1"],
        )
        reset_ws_guard(cfg)
        await ip_ban_manager.ban_ip("10.0.0.7", 3600)

        auth = FakeAuthorizer(valid_key=VALID_KEY)
        redis = FakeRedis()
        ws = FakeWS(client_host="127.0.0.1", xff="10.0.0.7", api_key=VALID_KEY)
        await run_multiplex(ws, auth, redis)
        assert ws.accepted is False
        assert ws.close_code == 4401
