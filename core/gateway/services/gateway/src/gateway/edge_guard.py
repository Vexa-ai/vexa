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
gateway down; ``redis_fail_open=True`` so a Redis outage degrades the rate limiter to
its in-memory per-process window instead of skipping it. ``lazy_init=True`` so the
heavy guard pipeline is built on first request, not at import (keeps ``create_app``
construction cheap and the conformance harness unaffected). Redis state reuses the
same ``REDIS_URL`` Vexa already runs, namespaced under ``vexa:guard:`` to avoid
colliding with Vexa's own keys (``ratelimit:``, ``gateway:token:``).
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import TYPE_CHECKING

# fastapi-guard 7.8.0: guard_websocket (the /ws connect check, called from run_multiplex in
# app.py, not from this module) is the last piece of the WS path that now lives entirely in
# the library. This module only needs the two symbols that build + install the HTTP
# middleware; SecurityConfig / SecurityMiddleware are re-exported at guard's top level.
from guard import SecurityConfig, SecurityMiddleware

from .config_preflight import ConfigError
from .obs import log_event
from .ratelimit import env_truthy

if TYPE_CHECKING:
    from fastapi import FastAPI

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
# "Vultr"] (guard-core 4.0.1, pinned here; guard_core.models.VALID_CLOUD_PROVIDERS is the same
# six names as a frozenset - both verified in this venv). Mirrored here (not imported) so a
# library-side rename doesn't quietly change what Vexa accepts out from under this error
# message.
_VALID_CLOUD_PROVIDERS = ("AWS", "Azure", "DigitalOcean", "GCP", "Linode", "Vultr")
_CLOUD_PROVIDER_BY_UPPER = {name.upper(): name for name in _VALID_CLOUD_PROVIDERS}


def _validate_block_cloud_providers(env: str, *, normalize: bool) -> set[str]:
    """Parse + validate ``GUARD_BLOCK_CLOUD_PROVIDERS`` against guard-core's closed set BEFORE
    it reaches ``SecurityConfig(...)``.

    On guard-core >= 3.15.0, an unrecognized ``block_cloud_providers`` entry is NOT
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

    On guard-core >= 3.15.0 an unrecognized entry raises inside ``SecurityConfig``
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

    Like ``block_cloud_providers`` (above), these three fields already raise on
    guard-core >= 3.15.0: ``guard_core/models.py``'s ``validate_ip_lists`` (lines 143-145, for
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


def _on_http_block(request: object, payload: dict) -> None:
    """``SecurityConfig.on_block`` callback for the HTTP path - the sibling of the WS side's
    ``ws_connect_rejected`` (``run_multiplex`` in ``app.py``), which has no HTTP equivalent
    today: a 429/403 from guard's ``SecurityMiddleware`` otherwise reaches only guard-core's
    own ``logging.getLogger("guard_core")``, never Vexa's ``logevent.v1`` stream.

    guard-core 4.0.0 fires this exactly once per blocked (or passive-mode-flagged) request,
    from three call sites (``core/checks/pipeline.py``, ``core/bypass/handler.py``,
    ``_utils/request_logging.py``, all via the shared ``_utils/block_events.py``), and
    ALREADY isolates it: ``invoke_block_hook`` wraps the call (and the awaited result, if the
    hook returns one) in ``try/except Exception``, logging any failure on the
    ``guard_core`` logger and never propagating it into the block response - so this callback
    does not need its own try/except on top. It also never fires at all for three check names:
    ``ON_BLOCK_EXCLUDED_CHECK_NAMES`` (``guard_core/_utils/block_events.py:11-13``) hard-excludes
    ``custom_request``, ``custom_validators`` and ``https_enforcement`` inside
    ``fire_block_hook`` itself. This gateway enables none of the three (grepped
    ``custom_request_check``, ``custom_validators`` and ``enforce_https`` in
    :func:`build_guard_config` - no matches), so the exclusion has no effect on what this
    gateway can observe today, but it holds regardless of what this config later turns on.
    ``payload`` always carries all of check_name, reason, trigger_info, passive_mode,
    client_ip, path, method, status_code (verified against
    ``guard_core/_utils/block_events.py``'s ``build_block_payload``, a fixed-key dict every
    call), but ``.get()`` is used anyway so a future guard-core payload shape change degrades
    to a missing field instead of a raised exception. ``status_code`` is ``None`` on the
    passive-mode path (no response is ever sent). ``trigger_info`` is deliberately NOT
    forwarded, though the field itself needs it least of anywhere it appears: it is the empty
    string for every check this gateway can trip except ``suspicious_activity`` (verified by
    reading ``ip_security.py`` and ``handlers/ratelimit_handler.py`` in this venv - neither
    ever passes a ``trigger_info`` argument to ``log_activity``, so its default, ``""``, is
    what reaches the payload). For ``suspicious_activity`` in ACTIVE mode
    (``guard_core/core/checks/implementations/suspicious_activity.py:111-116``, verified in
    this venv), the same attacker-controlled ``trigger_info`` - built from request header and
    query-param NAMES embedded verbatim, per ``guard_core/_utils/body_content_scan.py`` (lines
    179, 186, 230, 251 for headers; 144, 159 for query params) - is already folded into ``reason``
    (``sus_specs = f"{client_ip} - {trigger_info}"``, ``reason=f"Suspicious activity detected
    for IP: {sus_specs}"``), a field this callback DOES log; dropping the separate
    ``trigger_info`` payload key there would not remove the tainted text, only the second copy
    of it. Both ``reason`` and ``path`` can therefore carry attacker-controlled text, which is
    fine for this sink: ``log_event`` (``src/gateway/obs.py``) ``json.dumps``-encodes the whole
    envelope before it ever reaches stdout, the same way ``request_received`` already logs the
    raw ``path``. The gateway leaves ``enable_penetration_detection=False`` in
    :func:`build_guard_config` (confirmed, below), so ``suspicious_activity`` is dormant
    today - it never even joins the check pipeline, since this gateway also sets no
    route-level ``enable_suspicious_detection`` and no ``enable_dynamic_rules``, the only other
    two conditions that turn it on (``SuspiciousActivityCheck.applies_to``, same file).
    ``trigger_info`` stays excluded from the payload anyway: the callback is shared by every
    check guard-core ships, present and future, so it does not rely on today's config to keep
    it safe.

    Confirmed NOT to also fire for the ``/ws`` connect guard: ``guard_websocket``
    (``guard/websocket.py``) calls the guard-core primitives ``ip_ban_manager.is_ip_banned``,
    ``is_ip_allowed`` and ``check_rate_limit_by_ip`` directly and raises its own
    ``WebSocketException`` - none of that path references ``on_block`` or
    ``fire_block_hook`` (grepped the installed source; confirmed by executing a WS
    blacklist rejection and a WS rate-limit rejection through the test harness with a
    recording ``on_block`` - zero calls both times). So this event and ``ws_connect_rejected``
    never double up for the same connection.
    """
    log_event(
        "http_request_blocked",
        audience="system",
        level="warning",
        span="http",
        fields={
            "check_name": payload.get("check_name"),
            "reason": payload.get("reason"),
            "client_ip": payload.get("client_ip"),
            "path": payload.get("path"),
            "method": payload.get("method"),
            "status_code": payload.get("status_code"),
            "passive_mode": payload.get("passive_mode"),
        },
    )


def build_guard_config() -> SecurityConfig:
    """Build the guard ``SecurityConfig`` from env vars.

    Filter knobs (IP allow/deny, geo, cloud, trusted proxies) are opt-in and
    default to empty/off. Redis state uses the same ``REDIS_URL`` Vexa already
    runs, namespaced under ``vexa:guard:`` to avoid colliding with Vexa's own
    keys (``ratelimit:``, ``gateway:token:``). ``fail_secure=False`` so a guard
    check bug fails open instead of taking the public gateway down; ``redis_fail_open=True``
    so a Redis outage keeps the rate limiter running on its per-process window.
    ``on_block=`` :func:`_on_http_block` so every HTTP block (rate-limit 429, IP ban / blacklist
    403, cloud-provider or country 403, ...) also emits an ``http_request_blocked``
    ``logevent.v1`` line, the HTTP-side sibling of ``ws_connect_rejected`` (see that
    function's docstring for the full evidence).

    ``GUARD_IP_WHITELIST`` / ``GUARD_IP_BLACKLIST`` / ``GUARD_TRUSTED_PROXIES`` are pre-validated
    here (:func:`_validate_ip_or_cidr_csv`) and raise :class:`ConfigError` on a bad entry - they
    already raise on guard-core >= 3.15.0 too, so this pre-validation is load-bearing
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
        block_cloud_providers=_resolve_block_cloud_providers("GUARD_BLOCK_CLOUD_PROVIDERS"),
        trusted_proxies=_validate_ip_or_cidr_csv("GUARD_TRUSTED_PROXIES"),
        trust_x_forwarded_proto=_env_bool("GUARD_TRUST_X_FORWARDED_PROTO", False),
        enable_penetration_detection=False,
        enable_cors=False,
        security_headers={"enabled": False},
        fail_secure=False,
        # redis_fail_open=True: on a Redis outage the rate limiter keeps counting on its
        # in-memory per-process window (one WARNING per process) instead of skipping the
        # check. guard-core < 3.15.0 always fell back regardless of this flag; 3.15.0 makes
        # the limiter honor it, and the default (False) under fail_secure=False would skip
        # the rate limit entirely for every request until Redis is back. The open pin
        # (>=8.0) resolves to 4.0.1 on a fresh lock, so the flag is set explicitly to
        # keep one behavior on both sides of that boundary. Ban checks are unaffected:
        # is_ip_banned already fails open (not banned) under fail_secure=False.
        redis_fail_open=True,
        lazy_init=True,
        exclude_paths=_GUARD_EXCLUDE_PATHS,
        on_block=_on_http_block,
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
