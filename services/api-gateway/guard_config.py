"""fastapi-guard integration config for the Vexa api-gateway.

Wires guard's SecurityMiddleware as a layer complementary to the gateway's
existing per-key rate limiter: per-IP rate limiting, auto-IP-ban, and optional
IP/geo/cloud blocking (all env-driven, default off).

Two things are intentionally disabled here and handled by Vexa's own
middleware instead, to avoid duplicates / conflicting headers:

* CORS — Vexa already runs ``CORSMiddleware``.
* Security headers — Vexa's ``SecurityHeadersMiddleware`` carries VNC-specific
  CSP ``frame-ancestors`` logic guard cannot replicate.

Penetration / request-body WAF detection is OFF in this first pass: the gateway
proxies arbitrary user text (chat messages, meeting ``data`` JSON, transcript
shares) and signature-based body scanning would false-positive on legitimate
content. It is staged for a follow-up behind a passive-mode tuning pass.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from guard import SecurityConfig, SecurityMiddleware

if TYPE_CHECKING:
    from fastapi import FastAPI

_GUARD_REDIS_PREFIX_DEFAULT = "vexa:guard:"
_GUARD_RATE_LIMIT_RPM_DEFAULT = 600
_GUARD_RATE_LIMIT_WINDOW_DEFAULT = 60
_GUARD_AUTO_BAN_THRESHOLD_DEFAULT = 10
_GUARD_AUTO_BAN_DURATION_DEFAULT = 3600
_GUARD_REDIS_URL_DEFAULT = "redis://redis:6379/0"

# Paths that skip the guard pipeline entirely. Kept in sync with the per-key
# limiter's RATE_LIMIT_SKIP_PATHS so the two layers agree on what is public
# infrastructure vs. real API surface.
_GUARD_EXCLUDE_PATHS = [
    "/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/openapi.yaml",
    "/favicon.ico",
    "/static",
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
        blocked_countries=frozenset(_guard_csv("GUARD_BLOCKED_COUNTRIES")),
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

    Complementary to the per-key ``rate_limit_middleware``: that limiter is keyed
    by API token, guard's by client IP, with auto-banning of repeat offenders.
    The two gate different abuse shapes — many-tokens-from-one-IP (caught by
    per-IP + auto-ban) vs. one-token-across-many-IPs (caught by per-key) — and
    coexist; the per-key limiter is not replaced.
    """
    if not _env_bool("GUARD_ENABLED", True):
        return
    if config is None:
        config = build_guard_config()
    app.add_middleware(SecurityMiddleware, config=config)
