"""fastapi-guard integration config for the v0.12 gateway edge.

Wires guard's ``SecurityMiddleware`` as a layer complementary to the gateway's
existing per-user rate limiter (``ratelimit.py``): per-IP rate limiting, auto-IP-ban,
and optional IP/geo/cloud blocking (all env-driven, default off). This module owns
the ONE ``SecurityConfig`` and installs the ONE ``SecurityMiddleware`` both the HTTP
and the ``/ws`` path share: fastapi-guard's own ``guard_websocket`` (called directly
from ``run_multiplex`` in ``app.py``, not through this module) resolves that same
config at ``/ws`` connect time via its ``_find_security_config`` lookup on the
registered middleware. No Vexa-authored guard orchestration remains for either
path; this module's job ends at building the config and installing the middleware.

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

import ipaddress
import os
from typing import TYPE_CHECKING

# fastapi-guard 7.8.0: guard_websocket (the /ws connect check, called from run_multiplex in
# app.py, not from this module) is the last piece of the WS path that now lives entirely in
# the library. This module only needs the two symbols that build + install the HTTP
# middleware; SecurityConfig / SecurityMiddleware are re-exported at guard's top level.
from guard import SecurityConfig, SecurityMiddleware

from .config_preflight import ConfigError
from .ratelimit import env_truthy

if TYPE_CHECKING:
    from fastapi import FastAPI

_GUARD_REDIS_PREFIX_DEFAULT = "vexa:guard:"
_GUARD_RATE_LIMIT_RPM_DEFAULT = 600
_GUARD_RATE_LIMIT_WINDOW_DEFAULT = 60
_GUARD_AUTO_BAN_THRESHOLD_DEFAULT = 10
_GUARD_AUTO_BAN_DURATION_DEFAULT = 3600
_GUARD_REDIS_URL_DEFAULT = "redis://redis:6379/0"

# Paths that skip the guard pipeline entirely. guard matches these with
# ``url_path.startswith(path)`` — PREFIX matching, not exact — so a bare ``"/"``
# here would match EVERY path (everything starts with "/") and silently neuter the
# entire guard layer (no rate limit, no IP ban, nothing). The root landing is
# therefore intentionally NOT excluded: it is a cheap route and an IP spending its
# per-minute budget on it is harmless. Kept in sync with the per-key limiter's
# public-infrastructure surface otherwise (docs / openapi / health are public).
_GUARD_EXCLUDE_PATHS = [
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


# guard_core.CloudProvider = Literal["AWS", "GCP", "Azure"] (guard-core >=3.12.0, closed set,
# case-sensitive). Mirrored here (not imported) so a library-side rename doesn't quietly change
# what Vexa accepts out from under this error message.
_VALID_CLOUD_PROVIDERS = ("AWS", "GCP", "Azure")
_CLOUD_PROVIDER_BY_UPPER = {name.upper(): name for name in _VALID_CLOUD_PROVIDERS}


def _validate_block_cloud_providers(env: str) -> set[str]:
    """Parse + validate ``GUARD_BLOCK_CLOUD_PROVIDERS`` against guard-core's closed set BEFORE
    it reaches ``SecurityConfig(...)``.

    On the guard-core version this repo's uv.lock actually pins (3.4.0), ``block_cloud_providers``
    is a SILENT, case-sensitive filter (``models.py::validate_cloud_providers``):
    ``{sel for sel in v if sel.partition(":!")[0] in VALID_CLOUD_PROVIDERS}``. An unrecognized or
    wrong-case entry (the natural operator spelling, ``aws``) is just dropped, no error, no log.
    An unvalidated ``{"aws", "digitalocean"}`` silently becomes ``set()``, cloud blocking quietly
    off. The case-normalization below repairs that LIVE silent no-op on the pinned version:
    ``aws`` normalizes to ``AWS`` so it survives guard-core's filter instead of being dropped by
    it.

    A later guard-core (>=3.12.0) turns the same unrecognized-name mistake into a raise instead
    of a silent drop
    (``guard_core/_security_config_validators.py:_validate_block_cloud_providers_value``), a
    library stack trace deep inside ``SecurityConfig`` construction. Either way, today's silent
    drop or a future raise, this function is the fix: it validates at Vexa's own boundary with a
    message naming the var, the bad entry, and the exact accepted spellings, so a typo is caught
    the same way regardless of which guard-core version is installed.

    The ``:!region`` carve-out suffix (``NAME:!REGION``, see guard-core's ``cloud_handler.py``,
    which reads it via the same ``selector.partition(":!")`` used below) is preserved; only the
    provider-name half is case-normalized. The region half is validated too, not normalized:
    guard-core's real provider region strings are lowercase by convention (``us-east-1``,
    ``asia-south1``), with one synthetic exception, ``GLOBAL``, and ``is_cloud_ip`` matches the
    carve-out region with a plain, case-sensitive ``==``. An uppercase region would silently
    never match, making the carve-out a no-op, so it is rejected here instead of lowercased:
    rewriting a provider-defined string risks creating a NEW silent mismatch instead of fixing
    one.
    """
    result: set[str] = set()
    for entry in _guard_csv(env):
        provider, marker, region = entry.partition(":!")
        canonical = _CLOUD_PROVIDER_BY_UPPER.get(provider.upper())
        if canonical is None:
            raise ConfigError(
                f"{env} entry {entry!r} is not a recognized cloud provider. Accepted values "
                f"(case-insensitive): {', '.join(_VALID_CLOUD_PROVIDERS)}. Suffix ':!region' to "
                "carve out a region exception, e.g. 'AWS:!us-east-1'. Fix or remove the entry "
                "and restart."
            )
        if marker:
            if not region:
                raise ConfigError(
                    f"{env} entry {entry!r} has an empty region after ':!'. Name a region to "
                    "carve out, e.g. 'AWS:!us-east-1', or drop the ':!' suffix to block the "
                    "whole provider. Fix the entry and restart."
                )
            if region != "GLOBAL" and region != region.lower():
                raise ConfigError(
                    f"{env} entry {entry!r} has region {region!r}, which is not lowercase. "
                    "guard-core matches a carve-out region with a case-sensitive '==' against "
                    "real provider region strings, which are lowercase by convention (e.g. "
                    "'us-east-1', 'asia-south1'), with the single synthetic exception 'GLOBAL'. "
                    "An uppercase region here would silently never match, and the carve-out "
                    "would be a no-op. Use the lowercase region spelling or 'GLOBAL'. Fix the "
                    "entry and restart."
                )
        result.add(f"{canonical}{marker}{region}" if marker else canonical)
    return result


def _validate_ip_or_cidr_csv(env: str) -> list[str]:
    """Parse + validate ``GUARD_IP_WHITELIST`` / ``GUARD_IP_BLACKLIST`` / ``GUARD_TRUSTED_PROXIES``
    BEFORE they reach ``SecurityConfig(...)``.

    Unlike ``block_cloud_providers`` (above), these three fields ALREADY raise on the guard-core
    version this repo's uv.lock actually pins (3.4.0): ``models.py``'s ``validate_ip_lists`` /
    ``validate_trusted_proxies`` field validators run each entry through the same
    ``ipaddress.ip_address`` / ``ipaddress.ip_network`` parse and raise ``ValueError`` on
    failure, deep inside ``SecurityConfig`` construction, a bare library stack trace with no
    indication of which var or entry was wrong. So this pre-validation is load-bearing TODAY,
    not future-proofing against a later guard-core: its only job is error-message quality,
    surfacing the exact same failure as a Vexa :class:`ConfigError` naming the var and the
    offending entry, before the library ever sees the value.
    """
    entries = _guard_csv(env)
    for entry in entries:
        try:
            if "/" in entry:
                ipaddress.ip_network(entry, strict=False)
            else:
                ipaddress.ip_address(entry)
        except ValueError:
            raise ConfigError(
                f"{env} entry {entry!r} is not a valid IP address or CIDR range. Fix or remove "
                "the entry and restart."
            ) from None
    return entries


def _env_bool(env: str, default: bool) -> bool:
    """Read a boolean env var via the shared truthy set (``1/true/yes/on``, case-insensitive)."""
    raw = os.getenv(env)
    if raw is None:
        return default
    return env_truthy(raw)


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

    ``GUARD_IP_WHITELIST`` / ``GUARD_IP_BLACKLIST`` / ``GUARD_TRUSTED_PROXIES`` and
    ``GUARD_BLOCK_CLOUD_PROVIDERS`` are pre-validated here (:func:`_validate_ip_or_cidr_csv`,
    :func:`_validate_block_cloud_providers`) and raise :class:`ConfigError` on a bad entry. The
    IP-list fields already raise on the pinned guard-core (3.4.0) too, so this pre-validation is
    load-bearing for error-message quality today, not future-proofing. ``block_cloud_providers``
    on 3.4.0 is a SILENT case-sensitive filter instead (a bad or lowercase entry is dropped, not
    rejected), so the case-normalization here repairs a live silent no-op on the pinned version;
    a raise only arrives with a later guard-core. See each function's docstring for the
    field-by-field evidence.
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
        # enable_rate_limit_auto_ban feeds rate-limit violations into guard's auto-ban
        # engine (per-process "rate_limit" counter, ban reason "rate_limit_exceeded",
        # requires enable_ip_banning). ONE knob governs BOTH paths: the HTTP pipeline's
        # RateLimitCheck AND the WS connect primitive (check_rate_limit_by_ip) read it,
        # which is the point of the consolidation - the hand-rolled WS auto-ban this
        # replaces had no HTTP counterpart. Default ON to preserve the WS auto-ban the
        # hand-rolled guard shipped with; the HTTP path newly gains the same protection
        # (the documented expansion). Default off in guard-core itself; flip
        # GUARD_AUTO_BAN_RATE_LIMIT=false to disable both. See audit 5.5: without this,
        # auto_ban_threshold / auto_ban_duration were dead config on the rate-limit path.
        enable_rate_limit_auto_ban=_env_bool("GUARD_AUTO_BAN_RATE_LIMIT", True),
        auto_ban_threshold=_env_int(
            "GUARD_AUTO_BAN_THRESHOLD", _GUARD_AUTO_BAN_THRESHOLD_DEFAULT
        ),
        auto_ban_duration=_env_int(
            "GUARD_AUTO_BAN_DURATION", _GUARD_AUTO_BAN_DURATION_DEFAULT
        ),
        whitelist=_validate_ip_or_cidr_csv("GUARD_IP_WHITELIST") or None,
        blacklist=_validate_ip_or_cidr_csv("GUARD_IP_BLACKLIST"),
        blocked_countries=_guard_csv("GUARD_BLOCKED_COUNTRIES"),
        block_cloud_providers=_validate_block_cloud_providers(
            "GUARD_BLOCK_CLOUD_PROVIDERS"
        ),
        trusted_proxies=_validate_ip_or_cidr_csv("GUARD_TRUSTED_PROXIES"),
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

    Refuses to boot with :class:`ConfigError` when ``GUARD_WS_ENABLED=true`` but
    ``GUARD_ENABLED=false``: fastapi-guard's ``guard_websocket`` (the ``/ws`` connect
    check ``run_multiplex`` calls, see ``app.py``) resolves its ``SecurityConfig`` by
    looking up the ``SecurityMiddleware`` THIS function registers, so turning the HTTP
    guard off leaves the WS guard with no config to read. The env kill switch now turns
    both off together; WS-only guarding is no longer a valid configuration.

    Complementary to the per-user ``rate_limiter``: that limiter is keyed by API
    token, guard's by client IP, with auto-banning of repeat offenders. The two
    gate different abuse shapes — many-tokens-from-one-IP (caught by per-IP +
    auto-ban) vs. one-token-across-many-IPs (caught by per-key) — and coexist; the
    per-key limiter is not replaced.
    """
    guard_enabled = _env_bool("GUARD_ENABLED", True)
    ws_enabled = _env_bool("GUARD_WS_ENABLED", False)
    if ws_enabled and not guard_enabled:
        raise ConfigError(
            "GUARD_WS_ENABLED=true requires GUARD_ENABLED=true (it is currently false). "
            "fastapi-guard's guard_websocket, the /ws connect check, resolves its "
            "SecurityConfig from the SecurityMiddleware that GUARD_ENABLED registers, so "
            "the WS guard has no config to read once the HTTP guard is off. Set "
            "GUARD_ENABLED=true (or leave it unset, since true is the default) or set "
            "GUARD_WS_ENABLED=false, and restart."
        )
    if not guard_enabled:
        return
    if config is None:
        config = build_guard_config()
    app.add_middleware(SecurityMiddleware, config=config)
