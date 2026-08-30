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
import logging
import os
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Iterable, Optional

from guard import SecurityConfig, SecurityMiddleware

from .config_preflight import ConfigError
from .ratelimit import env_truthy

if TYPE_CHECKING:
    from fastapi import FastAPI, WebSocket

log = logging.getLogger(__name__)

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


# guard_core.models.CloudProvider = Literal["AWS", "GCP", "Azure", "DigitalOcean", "Linode",
# "Vultr"] (guard-core 3.15.0, pinned here; guard_core.models.VALID_CLOUD_PROVIDERS is the same
# six names as a frozenset - both verified in this venv). Mirrored here (not imported) so a
# library-side rename doesn't quietly change what Vexa accepts out from under this error
# message.
_VALID_CLOUD_PROVIDERS = ("AWS", "Azure", "DigitalOcean", "GCP", "Linode", "Vultr")
_CLOUD_PROVIDER_BY_UPPER = {name.upper(): name for name in _VALID_CLOUD_PROVIDERS}


def _validate_block_cloud_providers(env: str, *, normalize: bool) -> set[str]:
    """Parse + validate ``GUARD_BLOCK_CLOUD_PROVIDERS`` against guard-core's closed set BEFORE
    it reaches ``SecurityConfig(...)``.

    On the pinned guard-core (3.15.0), an unrecognized ``block_cloud_providers`` entry is NOT
    silently dropped: ``SecurityConfig.validate_cloud_providers`` (``guard_core/models.py:163-165``)
    delegates to a value validator that raises a pydantic ``ValidationError`` naming the bad
    entries, from deep inside ``SecurityConfig`` construction. Verified in this venv:
    ``SecurityConfig(block_cloud_providers={"aws"})`` raises ``Unknown cloud providers in
    block_cloud_providers: ['aws']. Valid: ['AWS', 'Azure', 'DigitalOcean', 'GCP', 'Linode',
    'Vultr']...``. So a bad entry fails the boot EITHER WAY, whether or not Vexa validates it
    itself.

    ``normalize`` controls only how strict the match is, not whether a bad entry raises:

    * ``normalize=False`` (the default - see :func:`_resolve_block_cloud_providers`): the
      provider half must be an exact-case member of ``_VALID_CLOUD_PROVIDERS``. A wrong-case
      entry (the natural operator spelling, ``aws``) or a genuinely unknown one still raises -
      that boot was going to fail in the library regardless - but as a Vexa :class:`ConfigError`
      naming the var, the bad entry, and the exact accepted spellings, instead of a bare
      pydantic stack trace from inside ``SecurityConfig``.
    * ``normalize=True`` (``GUARD_BLOCK_CLOUD_PROVIDERS_STRICT=true``): the provider half is
      case-normalized first (``aws`` -> ``AWS``), so a wrong-case entry that would otherwise
      raise now constructs and blocks live. This is the one genuinely behavior-changing part -
      it turns a boot failure into real cloud enforcement - so it stays opt-in behind STRICT.

    The ``:!region`` carve-out suffix (``NAME:!REGION``, see guard-core's ``cloud_handler.py``,
    which reads it via the same ``selector.partition(":!")`` used below) is preserved either
    way; only the provider-name half is ever case-normalized. The region half is validated too,
    not normalized: guard-core's real provider region strings are lowercase by convention
    (``us-east-1``, ``asia-south1``), with one synthetic exception, ``GLOBAL``, and
    ``CloudManager.is_cloud_ip`` (``guard_core/handlers/cloud_handler.py``) checks a carve-out
    region for exact, case-sensitive membership against those lowercase strings. An uppercase
    region here would silently never match, making the carve-out a no-op, so it is rejected here
    instead of lowercased: rewriting a provider-defined string risks creating a NEW silent
    mismatch instead of fixing one. The carve-out is functional on the pinned guard-core:
    ``CloudManager._refresh_providers`` (same module) iterates
    ``_bare_provider_names(providers)`` (``guard_core/handlers/_cloud_provider_registry.py``),
    which strips the ``:!region`` selector before fetching ranges, so a carved-out provider
    still gets its IP ranges refreshed - verified by reading both in this venv.
    """
    result: set[str] = set()
    for entry in _guard_csv(env):
        provider, marker, region = entry.partition(":!")
        if normalize:
            canonical = _CLOUD_PROVIDER_BY_UPPER.get(provider.upper())
        else:
            canonical = provider if provider in _VALID_CLOUD_PROVIDERS else None
        if canonical is None:
            raise ConfigError(
                f"{env} entry {entry!r} is not a recognized cloud provider. Accepted values: "
                f"{', '.join(_VALID_CLOUD_PROVIDERS)}"
                f"{' (case-insensitive)' if normalize else ''}. Fix or remove the entry and "
                "restart."
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
                    "guard-core matches a carve-out region against real provider region "
                    "strings, which are lowercase by convention (e.g. 'us-east-1', "
                    "'asia-south1'), with the single synthetic exception 'GLOBAL', using an "
                    "exact, case-sensitive comparison. An uppercase region here would silently "
                    "never match, and the carve-out would be a no-op. Use the lowercase region "
                    "spelling or 'GLOBAL'. Fix the entry and restart."
                )
        result.add(f"{canonical}{marker}{region}" if marker else canonical)
    return result


def _resolve_block_cloud_providers(env: str) -> set[str]:
    """Build the ``block_cloud_providers`` value passed to ``SecurityConfig``, gated by
    ``GUARD_BLOCK_CLOUD_PROVIDERS_STRICT`` (default false).

    On the pinned guard-core (3.15.0) an unrecognized entry raises inside ``SecurityConfig``
    construction no matter what Vexa does (see :func:`_validate_block_cloud_providers`'s
    docstring for the evidence), so boundary validation is unconditional here - there is no
    passthrough path left to gate. ``GUARD_BLOCK_CLOUD_PROVIDERS_STRICT`` only chooses how
    strict the match is:

    OFF (default): exact-case match against ``_VALID_CLOUD_PROVIDERS``. A wrong-case or unknown
    entry still raises - that boot was failing in the library regardless - as a legible Vexa
    :class:`ConfigError` naming the var, the entry, and the accepted spellings, instead of a
    bare pydantic stack trace.

    ON: case-insensitive normalization to guard-core's canonical spelling (``aws`` -> ``AWS``),
    so a wrong-case entry that would otherwise raise now constructs and blocks live. That is a
    genuine behavior change (inert-on-boot-failure becomes active enforcement), so it stays
    opt-in.
    """
    strict = _env_bool("GUARD_BLOCK_CLOUD_PROVIDERS_STRICT", False)
    return _validate_block_cloud_providers(env, normalize=strict)


def _validate_ip_or_cidr_csv(env: str) -> list[str]:
    """Parse + validate ``GUARD_IP_WHITELIST`` / ``GUARD_IP_BLACKLIST`` / ``GUARD_TRUSTED_PROXIES``
    BEFORE they reach ``SecurityConfig(...)``.

    Like ``block_cloud_providers`` (above), these three fields already raise on the pinned
    guard-core (3.15.0): ``guard_core/models.py``'s ``validate_ip_lists`` (lines 143-145, for
    ``whitelist``/``blacklist``) and ``validate_trusted_proxies`` (lines 147-150) field
    validators run each entry through the same ``ipaddress``-based parse and raise a pydantic
    ``ValidationError`` on failure, from deep inside ``SecurityConfig`` construction, a bare
    library stack trace with no indication of which var or entry was wrong. Verified in this
    venv: ``SecurityConfig(whitelist=["not-an-ip"])`` raises ``Invalid IP or CIDR range:
    not-an-ip``. So this pre-validation is load-bearing on the pinned version: its job is
    error-message quality, surfacing the exact same failure as a Vexa :class:`ConfigError`
    naming the var and the offending entry, before the library ever sees the value.
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


def _ip_matches(client_ip: str, entries: Iterable[str]) -> bool:
    """True iff ``client_ip`` is contained in any CIDR/IP in ``entries``.

    Tracks the HTTP path's CIDR semantics (fastapi-guard accepts CIDR ranges in
    whitelist/blacklist/trusted_proxies). Each entry is parsed with
    ``ip_network(entry, strict=False)`` so a bare IP becomes a ``/32`` (v4) / ``/128`` (v6)
    and still matches itself. Diverges from the HTTP path on malformed entries: the HTTP
    ``_ip_in_list`` raises on a malformed CIDR-with-``/``, whereas this helper SKIPS a
    malformed entry so a bad value does not take the WS path down (fail-open, consistent
    with ``fail_secure=False`` — a typo'd ``GUARD_IP_BLACKLIST`` entry silently does not
    block, the safe-direction tradeoff). A malformed ``client_ip`` likewise matches
    nothing (returns False).
    """
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue  # skip malformed entry (permissive — do not raise)
        if addr in net:
            return True
    return False


def build_guard_config() -> SecurityConfig:
    """Build the guard ``SecurityConfig`` from env vars.

    Filter knobs (IP allow/deny, geo, cloud, trusted proxies) are opt-in and
    default to empty/off. Redis state uses the same ``REDIS_URL`` Vexa already
    runs, namespaced under ``vexa:guard:`` to avoid colliding with Vexa's own
    keys (``ratelimit:``, ``gateway:token:``). ``fail_secure=False`` so a guard
    check bug fails open instead of taking the public gateway down.

    ``GUARD_IP_WHITELIST`` / ``GUARD_IP_BLACKLIST`` / ``GUARD_TRUSTED_PROXIES`` are pre-validated
    here (:func:`_validate_ip_or_cidr_csv`) and raise :class:`ConfigError` on a bad entry - they
    already raise on the pinned guard-core (3.15.0) too, so this pre-validation is load-bearing
    for error-message quality, not future-proofing.

    ``GUARD_BLOCK_CLOUD_PROVIDERS`` goes through :func:`_resolve_block_cloud_providers` instead,
    gated by ``GUARD_BLOCK_CLOUD_PROVIDERS_STRICT`` (default false). On the pinned guard-core, an
    unrecognized or wrong-case entry raises inside ``SecurityConfig`` construction regardless of
    what Vexa does, so by default the value is validated (exact-case, no normalization) and a
    bad entry raises a legible Vexa :class:`ConfigError` instead of a bare pydantic stack trace -
    that boot fails either way. STRICT additionally case-normalizes (``aws`` -> ``AWS``), which
    turns a would-be boot failure into live cloud enforcement, so it stays opt-in. See
    :func:`_resolve_block_cloud_providers` and :func:`_validate_block_cloud_providers` for the
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
        block_cloud_providers=_resolve_block_cloud_providers("GUARD_BLOCK_CLOUD_PROVIDERS"),
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

        # Whitelist bypass (explicit allow short-circuits everything) — CIDR-aware.
        if cfg.whitelist and _ip_matches(client_ip, cfg.whitelist):
            return True
        # Blacklist deny — CIDR-aware.
        if _ip_matches(client_ip, cfg.blacklist or []):
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
    if not _ip_matches(connecting_ip, config.trusted_proxies):
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
