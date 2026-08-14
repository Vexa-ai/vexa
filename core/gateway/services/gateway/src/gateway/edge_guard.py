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

import ipaddress
import os
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any, Optional, cast

from guard import SecurityConfig, SecurityMiddleware, ip_ban_manager

# is_ip_allowed / extract_client_ip are not exported at guard's top level yet (ask filed
# upstream - see the WS guard hook section below); import straight from guard_core. The
# pinned guard-core (3.4.0, uv.lock) has ``is_ip_allowed`` (plain bool) rather than the
# newer ``check_ip_access`` (returns a reasoned ``IpAccessResult``) some later guard-core
# versions add - same whitelist/blacklist/cloud-provider semantics, just a plainer return.
from guard_core.utils import extract_client_ip, is_ip_allowed

from .config_preflight import ConfigError
from .ratelimit import env_truthy

if TYPE_CHECKING:
    from fastapi import FastAPI, WebSocket
    from guard_core.protocols.request_protocol import GuardRequest

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
        auto_ban_threshold=_env_int(
            "GUARD_AUTO_BAN_THRESHOLD", _GUARD_AUTO_BAN_THRESHOLD_DEFAULT
        ),
        auto_ban_duration=_env_int(
            "GUARD_AUTO_BAN_DURATION", _GUARD_AUTO_BAN_DURATION_DEFAULT
        ),
        whitelist=_validate_ip_or_cidr_csv("GUARD_IP_WHITELIST") or None,
        blacklist=_validate_ip_or_cidr_csv("GUARD_IP_BLACKLIST"),
        blocked_countries=_guard_csv("GUARD_BLOCKED_COUNTRIES"),
        block_cloud_providers=_validate_block_cloud_providers("GUARD_BLOCK_CLOUD_PROVIDERS"),
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
# client IP and calls :func:`ws_guard_check` to deny over-limit/banned IPs at connect.
#
# Phase 1 (this section) replaced the pieces guard's library already covers: IP-list /
# cloud-provider matching (``is_ip_allowed``), the ban store (``ip_ban_manager`` -
# process-wide and Redis-shared with the HTTP middleware when ``GUARD_ENABLE_REDIS`` is
# on), and client-IP resolution (``extract_client_ip`` via the minimal
# ``_WsGuardRequest`` adapter below). What is LEFT hand-rolled is the rate limit itself:
# SecurityMiddleware exposes no reusable programmatic rate-limit primitive (its
# ``dispatch`` is bound to an HTTP ``Request`` and the internal rate-limit check needs a
# full ``GuardRequest`` + pipeline). So the rate-limit half is a MINIMAL standalone
# limiter:
#
#   ponytail: standalone WS rate limiter - in-process buckets, NOT
#   SecurityMiddleware's own rate-limit counters (the ban store is shared via
#   ip_ban_manager, but the sliding-window bucket is not). Promote to fastapi-guard's
#   native WS support if/when upstream adds a reusable rate-limit primitive (ASK 2).

_WS_GUARD: Optional["_WsGuard"] = None


class _WsGuardRequest:
    """Minimal WebSocket -> ``GuardRequest`` adapter, for :func:`resolve_ws_client_ip`.

    Starlette's ``WebSocket`` shares the ``HTTPConnection`` base with ``Request``
    (``.client`` / ``.headers`` / ``.state`` all present), so this exposes ONLY the
    three members ``guard_core.utils.extract_client_ip`` actually reads - not the full
    ``GuardRequest`` protocol (``url_path``, ``method``, ``body``, ...) a general-purpose
    adapter needs. Mirrors fastapi-guard's own HTTP adapter, ``StarletteGuardRequest``
    (``guard/adapters.py``), which implements the whole protocol because the HTTP
    pipeline needs all of it; this one doesn't, so it doesn't. An official WS
    ``GuardRequest`` adapter is a filed upstream ask - if/when it ships, this class (and
    the ``cast`` at its one call site) goes away too.
    """

    __slots__ = ("_ws",)

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    @property
    def client_host(self) -> str | None:
        return self._ws.client.host if self._ws.client else None

    @property
    def headers(self) -> Any:
        return self._ws.headers

    @property
    def state(self) -> Any:
        return self._ws.state


class _WsGuard:
    """Per-IP guard for the ``/ws`` connect path: library-backed IP-list/ban decisions
    (SWAP A/B), in-process sliding-window rate limit (still hand-rolled, see the module
    comment above).

    Mirrors the HTTP layer's knobs (rate limit, auto-ban threshold/duration,
    blacklist, whitelist) from the SAME :class:`SecurityConfig` the HTTP middleware
    uses, so one env surface governs both.
    """

    __slots__ = ("_config", "_rl", "_ban_counts")

    def __init__(self, config: SecurityConfig) -> None:
        self._config = config
        # ip -> sliding-window timestamps (monotonic) - the still-hand-rolled half.
        self._rl: defaultdict[str, deque[float]] = defaultdict(deque)
        # ip -> count of rate-limit violations toward auto-ban. Stays in-process (it
        # feeds the threshold, part of the rate-limit half phase 2 replaces); only the
        # ban STORE itself moved to ip_ban_manager (SWAP B) below.
        self._ban_counts: defaultdict[str, int] = defaultdict(int)

    async def check(self, client_ip: str) -> bool:
        """Return True if the IP may connect, False if over-limit or banned."""
        cfg = self._config

        # Ban check first, unconditional - mirrors guard-core's IpSecurityCheck order
        # (its ban lookup runs before the whitelist/blacklist decision), so an actively
        # banned IP stays blocked even if it is also whitelisted. SWAP B: the ban STORE
        # is now ip_ban_manager (guard_core.handlers.ipban_handler) - process-wide and
        # Redis-shared with the HTTP middleware when GUARD_ENABLE_REDIS is on, closing
        # the multi-replica gap the old in-process ``_bans`` dict had (helm defaults the
        # gateway to replicaCount 2).
        if await ip_ban_manager.is_ip_banned(client_ip):
            return False

        # IP access (whitelist/blacklist/cloud-provider) via the library (SWAP A),
        # replacing the hand-copied ``_ip_matches``. is_ip_allowed parses each entry
        # strictly and only ever fails CLOSED (blocks) on a malformed one, unlike the
        # old ``_ip_matches``, which deliberately skipped a malformed entry (fail-open).
        # That fail-open rationale is now obsolete: ``_validate_ip_or_cidr_csv``
        # (env-validation, merged ahead of this swap) guarantees every whitelist /
        # blacklist / trusted-proxy entry is a valid IP or CIDR before it ever reaches
        # ``SecurityConfig``, so a malformed entry can no longer get here at all - the
        # boot refuses first.
        #
        # CAUTION (audit 5.4, deliberate): a non-empty whitelist is EXCLUSIVE here - an
        # IP not on it is blocked outright, the blacklist is never consulted - matching
        # guard-core's HTTP semantics exactly. The old hand-rolled check only used the
        # whitelist as a bypass fast path: a listed IP passed immediately, but an
        # UNLISTED IP just fell through to the (often empty) blacklist and was allowed.
        # See ``test_whitelist_exclusive_blocks_unlisted_ip`` for the pinned new
        # behavior.
        if not await is_ip_allowed(client_ip, cfg):
            return False
        # allowed + a non-empty whitelist means the IP matched it (is_ip_allowed never
        # falls through to the blacklist once a whitelist is set) - mirrors guard-core's
        # own is_whitelisted computation (core/checks/implementations/ip_security.py).
        is_whitelisted = bool(cfg.whitelist)
        if is_whitelisted:
            # A whitelisted IP also skips rate limiting, mirroring guard-core's
            # RateLimitCheck (``if request.state.is_whitelisted: return None``).
            return True

        # Per-IP sliding-window rate limit - still hand-rolled (ASK 2: no reusable
        # library primitive yet for this half).
        if cfg.enable_rate_limiting:
            now = time.monotonic()
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
                        # SWAP B: ban CREATION moves to ip_ban_manager too. Reset-on-set:
                        # after the ban window expires the offender starts a fresh cycle
                        # (clean "auto-ban for a window, then fresh budget" semantics) -
                        # without this, ban_counts sits at the threshold and the first
                        # over-limit post-expiry immediately re-bans for a full duration.
                        await ip_ban_manager.ban_ip(
                            client_ip, int(cfg.auto_ban_duration)
                        )
                        self._ban_counts[client_ip] = 0
                        bucket.clear()
                return False
            bucket.append(now)
        return True


def reset_ws_guard(config: SecurityConfig | None = None) -> None:
    """Rebuild the WS guard singleton (tests call this to isolate behavior)."""
    global _WS_GUARD
    _WS_GUARD = _WsGuard(config or build_guard_config())


async def ws_guard_check(ws: WebSocket) -> bool:
    """Resolve the client IP from ``ws`` (using the singleton's trusted-proxies/XFF config)
    and check it against the WS guard. Returns True if the connect may proceed.

    This is the composed entry point ``run_multiplex`` calls — it uses the SAME config for
    IP resolution and the check so the two never disagree. Tests isolate behavior via
    :func:`reset_ws_guard` (which swaps the singleton + its config together).
    """
    global _WS_GUARD
    if _WS_GUARD is None:
        _WS_GUARD = _WsGuard(build_guard_config())
    client_ip = await resolve_ws_client_ip(ws, _WS_GUARD._config)
    return await _WS_GUARD.check(client_ip)


async def resolve_ws_client_ip(ws: WebSocket, config: SecurityConfig) -> str:
    """Resolve the client IP from a WebSocket via guard_core's own ``extract_client_ip``
    (SWAP C) - the SAME function the HTTP path uses, wrapped through the minimal
    ``_WsGuardRequest`` adapter above, so WS and HTTP can never disagree on trusted-proxy
    / X-Forwarded-For handling. Replaces the hand-copied peer-trust + XFF-depth walk that
    used to live here (deleted; see git history for the old logic).

    When the TCP peer is NOT a trusted proxy, the XFF header is ignored and the peer IP
    is used - so a spoofed XFF from an untrusted source does NOT rotate the
    rate-limit/ban budget (the A4 spoofed-XFF sub-case). When the peer IS a trusted
    proxy, the client IP is taken from the ``X-Forwarded-For`` chain at
    ``config.trusted_proxy_depth`` entries back from the RIGHTMOST one - both behaviors
    now live in ``extract_client_ip`` itself.
    """
    return await extract_client_ip(cast("GuardRequest", _WsGuardRequest(ws)), config)
