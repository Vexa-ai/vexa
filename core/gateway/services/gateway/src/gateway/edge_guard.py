"""fastapi-guard integration config for the v0.12 gateway edge.

Wires guard's ``SecurityMiddleware`` as a layer complementary to the gateway's
existing per-user rate limiter (``ratelimit.py``): per-IP rate limiting, auto-IP-ban,
and optional IP/geo/cloud blocking (all env-driven, default off).

Two things are intentionally disabled here and handled by Vexa's own middleware
instead (or, on the 0.12 carve, NOT yet shipped — the rulings stay so a future
addition can't double up), to avoid duplicates / conflicting headers:

* CORS — Vexa already runs ``CORSMiddleware`` on the 0.10.x gateway. The 0.12 carve
  ships NEITHER CORS nor security-headers today, but the rulings are kept OFF so a
  future addition at this edge can't double up (guard OFF + a new CORS layer ON, not
  both ON).
* Security headers — Vexa's ``SecurityHeadersMiddleware`` (0.10.x) carries Vexa-specific
  CSP ``frame-ancestors`` logic guard cannot replicate. Moot on 0.12 (no such middleware
  ships yet), but kept OFF for the same future-proofing reason.

Penetration / request-body WAF detection is OFF in this first pass: the gateway
proxies arbitrary user text (chat messages, meeting ``data`` JSON, transcript
shares) and signature-based body scanning would false-positive on legitimate
content. It is staged for a follow-up behind a passive-mode tuning pass.

``fail_secure=False`` so a guard check bug fails open instead of taking the public
gateway down. ``lazy_init=True`` so the heavy guard pipeline is built on first
request, not at import (keeps ``create_app`` construction cheap and the conformance
harness unaffected). Redis state reuses the same ``REDIS_URL`` Vexa already runs,
namespaced under ``vexa:guard:`` to avoid colliding with Vexa's own keys
(``ratelimit:``, ``gateway:token:``).
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Optional

from guard import SecurityConfig, SecurityMiddleware

if TYPE_CHECKING:
    from fastapi import FastAPI, WebSocket

_GUARD_REDIS_PREFIX_DEFAULT = "vexa:guard:"
_GUARD_RATE_LIMIT_RPM_DEFAULT = 600
_GUARD_RATE_LIMIT_WINDOW_DEFAULT = 60
_GUARD_AUTO_BAN_THRESHOLD_DEFAULT = 10
_GUARD_AUTO_BAN_DURATION_DEFAULT = 3600
_GUARD_REDIS_URL_DEFAULT = "redis://redis:6379/0"

# Paths that skip the guard pipeline entirely. Kept in sync with the per-key
# limiter's public-infrastructure surface so the two layers agree on what is public
# infrastructure vs. real API surface. Adds ``/health`` (the liveness probe) so
# health monitors never trip the guard.
_GUARD_EXCLUDE_PATHS = [
    "/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/openapi.yaml",
    "/favicon.ico",
    "/static",
    "/health",
]


def _guard_csv(env: str) -> list[str]:
    """Parse a comma-separated env var into a stripped, non-empty list."""
    return [value.strip() for value in os.getenv(env, "").split(",") if value.strip()]


def _env_bool(env: str, default: bool) -> bool:
    """Read a boolean env var (``true``/``false``, case-insensitive)."""
    raw = os.getenv(env)
    if raw is None:
        return default
    return raw.strip().lower() == "true"


def _env_int(env: str, default: int) -> int:
    """Read an int env var, falling back to ``default`` on missing/invalid input."""
    raw = os.getenv(env)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def build_guard_config() -> SecurityConfig:
    """Build the guard ``SecurityConfig`` from env vars.

    Filter knobs (IP allow/deny, geo, cloud, trusted proxies) are opt-in and
    default to empty/off. Redis state uses the same ``REDIS_URL`` Vexa already
    runs, namespaced under ``vexa:guard:`` to avoid colliding with Vexa's own
    keys (``ratelimit:``, ``gateway:token:``). ``fail_secure=False`` so a guard
    check bug fails open instead of taking the public gateway down.
    """
    rate_limit_rpm = _env_int("GUARD_RATE_LIMIT_RPM", _GUARD_RATE_LIMIT_RPM_DEFAULT)
    return SecurityConfig(
        enable_redis=_env_bool("GUARD_ENABLE_REDIS", True),
        redis_url=os.getenv("REDIS_URL", _GUARD_REDIS_URL_DEFAULT),
        redis_prefix=os.getenv("GUARD_REDIS_PREFIX", _GUARD_REDIS_PREFIX_DEFAULT),
        enable_rate_limiting=rate_limit_rpm > 0,
        rate_limit=rate_limit_rpm,
        rate_limit_window=_env_int(
            "GUARD_RATE_LIMIT_WINDOW", _GUARD_RATE_LIMIT_WINDOW_DEFAULT
        ),
        enable_ip_banning=True,
        auto_ban_threshold=_env_int(
            "GUARD_AUTO_BAN_THRESHOLD", _GUARD_AUTO_BAN_THRESHOLD_DEFAULT
        ),
        auto_ban_duration=_env_int(
            "GUARD_AUTO_BAN_DURATION", _GUARD_AUTO_BAN_DURATION_DEFAULT
        ),
        whitelist=_guard_csv("GUARD_IP_WHITELIST") or None,
        blacklist=_guard_csv("GUARD_IP_BLACKLIST"),
        blocked_countries=_guard_csv("GUARD_BLOCKED_COUNTRIES"),
        block_cloud_providers=set(_guard_csv("GUARD_BLOCK_CLOUD_PROVIDERS")),
        trusted_proxies=_guard_csv("GUARD_TRUSTED_PROXIES"),
        trust_x_forwarded_proto=_env_bool("GUARD_TRUST_X_FORWARDED_PROTO", False),
        enable_penetration_detection=False,
        enable_cors=False,
        security_headers={"enabled": False},
        fail_secure=False,
        lazy_init=True,
        exclude_paths=_GUARD_EXCLUDE_PATHS,
    )


def apply_guard(app: FastAPI, config: SecurityConfig | None = None) -> None:
    """Add fastapi-guard's ``SecurityMiddleware`` to the gateway.

    No-op when ``GUARD_ENABLED=false`` (operator kill switch). When ``config`` is
    omitted it is built from env via :func:`build_guard_config`.

    Complementary to the per-user ``rate_limiter``: that limiter is keyed by API
    token, guard's by client IP, with auto-banning of repeat offenders. The two
    gate different abuse shapes — many-tokens-from-one-IP (caught by per-IP +
    auto-ban) vs. one-token-across-many-IPs (caught by per-key) — and coexist; the
    per-key limiter is not replaced.
    """
    if not _env_bool("GUARD_ENABLED", True):
        return
    if config is None:
        config = build_guard_config()
    app.add_middleware(SecurityMiddleware, config=config)


# ── WS guard hook ─────────────────────────────────────────────────────────────
# HTTP ``SecurityMiddleware`` does NOT intercept the ``/ws`` multiplex (Starlette
# middleware is HTTP-only). When ``GUARD_WS_ENABLED=true`` (default false — opt-in,
# since WS guard is beyond the drafted floor), ``run_multiplex`` resolves the
# client IP and calls :func:`check_ws` to deny over-limit/banned IPs at connect.
#
# SecurityMiddleware exposes NO reusable programmatic IP-check callable (its
# ``dispatch`` is bound to an HTTP ``Request`` and the internal ``SecurityCheckPipeline``
# needs a full ``GuardRequest``). So this is a MINIMAL standalone limiter:
#
#   ponytail: standalone WS limiter — shares the vexa:guard: redis namespace (when
#   Redis is on, the HTTP middleware persists there) but NOT SecurityMiddleware's
#   in-process counters; the WS path keeps its OWN in-process buckets. Promote to
#   fastapi-guard's native WS support if/when upstream adds a reusable IP-check.

_WS_GUARD: Optional["_WsGuard"] = None


class _WsGuard:
    """In-memory per-IP rate limiter + auto-ban for the ``/ws`` connect path.

    Mirrors the HTTP layer's knobs (rate limit, auto-ban threshold/duration,
    blacklist, whitelist) from the SAME :class:`SecurityConfig` the HTTP middleware
    uses, so one env surface governs both. In-process only — when Redis is enabled
    the HTTP middleware shares state across processes via Redis; this WS guard does
    not (the ceiling named above).
    """

    __slots__ = ("_config", "_rl", "_bans", "_ban_counts")

    def __init__(self, config: SecurityConfig) -> None:
        self._config = config
        # ip -> sliding-window timestamps (monotonic)
        self._rl: defaultdict[str, deque[float]] = defaultdict(deque)
        # ip -> unban monotonic time
        self._bans: dict[str, float] = {}
        # ip -> count of rate-limit violations (toward auto-ban)
        self._ban_counts: defaultdict[str, int] = defaultdict(int)

    def check(self, client_ip: str) -> bool:
        """Return True if the IP may connect, False if over-limit or banned."""
        now = time.monotonic()
        cfg = self._config

        # Whitelist bypass (explicit allow short-circuits everything).
        if cfg.whitelist and client_ip in cfg.whitelist:
            return True
        # Blacklist deny.
        if client_ip in (cfg.blacklist or []):
            return False
        # Active ban?
        unban = self._bans.get(client_ip)
        if unban is not None:
            if now < unban:
                return False
            del self._bans[client_ip]

        # Per-IP sliding-window rate limit.
        if cfg.enable_rate_limiting:
            window = float(cfg.rate_limit_window)
            limit = int(cfg.rate_limit)
            bucket = self._rl[client_ip]
            cutoff = now - window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                # Over limit → count toward auto-ban; ban when the threshold is reached.
                # Auto-ban only fires when IP banning is enabled (the config knob is
                # otherwise ignored, which would let banning slip in via the back door).
                if cfg.enable_ip_banning:
                    self._ban_counts[client_ip] += 1
                    if self._ban_counts[client_ip] >= int(cfg.auto_ban_threshold):
                        # Reset on set: after the ban window expires the offender starts a
                        # fresh cycle (clean "auto-ban for a window, then fresh budget"
                        # semantics) — without this, ban_counts sits at the threshold and the
                        # first over-limit post-expiry immediately re-bans for a full duration.
                        self._bans[client_ip] = now + float(cfg.auto_ban_duration)
                        self._ban_counts[client_ip] = 0
                        bucket.clear()
                return False
            bucket.append(now)
        return True


def reset_ws_guard(config: SecurityConfig | None = None) -> None:
    """Rebuild the WS guard singleton (tests call this to isolate behavior)."""
    global _WS_GUARD
    _WS_GUARD = _WsGuard(config or build_guard_config())


def check_ws(client_ip: str) -> bool:
    """Check whether ``client_ip`` may open a WS connection.

    The singleton is built from env on first call and reused across connects so
    in-process counters persist. Tests force a fresh singleton via :func:`reset_ws_guard`.
    """
    global _WS_GUARD
    if _WS_GUARD is None:
        _WS_GUARD = _WsGuard(build_guard_config())
    return _WS_GUARD.check(client_ip)


def ws_guard_check(ws: WebSocket) -> bool:
    """Resolve the client IP from ``ws`` (using the singleton's trusted-proxies/XFF config)
    and check it against the WS guard. Returns True if the connect may proceed.

    This is the composed entry point ``run_multiplex`` calls — it uses the SAME config for
    IP resolution and the check so the two never disagree. Tests isolate behavior via
    :func:`reset_ws_guard` (which swaps the singleton + its config together).
    """
    global _WS_GUARD
    if _WS_GUARD is None:
        _WS_GUARD = _WsGuard(build_guard_config())
    client_ip = resolve_ws_client_ip(ws, _WS_GUARD._config)
    return _WS_GUARD.check(client_ip)


def resolve_ws_client_ip(ws: WebSocket, config: SecurityConfig) -> str:
    """Resolve the client IP from a WebSocket mirroring guard's HTTP path
    (``guard_core.utils.extract_client_ip``).

    When the TCP peer is NOT a trusted proxy, the XFF header is ignored and the
    peer IP is used — so a spoofed XFF from an untrusted source does NOT rotate
    the rate-limit/ban budget (the A4 spoofed-XFF sub-case).

    When the peer IS a trusted proxy, the client IP is taken from the
    ``X-Forwarded-For`` chain at depth ``config.trusted_proxy_depth`` counting
    back from the RIGHTMOST entry (``ips[-trusted_proxy_depth]`` — one hop back
    from the trusted peer when depth=1, the default). This matches guard_core's
    ``_extract_from_forwarded_header`` exactly; the LEFTMOST entry is the most
    attacker-spoofable one and must not be keyed on.
    """
    connecting_ip = ws.client.host if ws.client else "unknown"
    if not config.trusted_proxies:
        return connecting_ip
    if connecting_ip not in config.trusted_proxies:
        # Untrusted peer: ignore XFF so a spoofed header cannot rotate the budget.
        return connecting_ip
    xff = ws.headers.get("x-forwarded-for")
    if not xff:
        return connecting_ip
    ips = [entry.strip() for entry in xff.split(",")]
    depth = max(1, int(getattr(config, "trusted_proxy_depth", 1) or 1))
    if len(ips) >= depth:
        return ips[-depth] or connecting_ip
    return connecting_ip
