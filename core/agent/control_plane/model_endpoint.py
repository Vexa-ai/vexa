"""model_endpoint.py — ONE predicate for "this subject's model config points somewhere else", and
the operator gate on where "somewhere else" is allowed to be.

TWO DEFECTS LIVE HERE, and they are the same defect seen from two sides (F84 · F93).

**The credential could ride to a foreign host.** ``overlay_model_config`` stamped
``ANTHROPIC_BASE_URL`` from a subject's Settings → Models config but set ``ANTHROPIC_AUTH_TOKEN``
only when that subject had *also* supplied a key. ``build_unit_env`` then backfills the
MODEL_AUTH_ENV_ALLOWLIST from agent-api's OWN environment for every key still absent — so
``mode=custom`` + a base_url + an EMPTY key handed the deployment's brokered credential to whatever
endpoint the subject named. There was no scheme or host check either, so that endpoint could be
``http://admin-api:8001``, ``http://redis:6379`` or ``http://169.254.169.254/`` — SSRF from inside
the control plane, with a live token attached.

The fix is two rules, and both are enforced here rather than at the call sites:

  1. **A custom endpoint always carries the subject's OWN credential — empty string included.**
     Absence is what the backfill fills; an explicit empty string is not absence. So a keyless
     gateway gets an empty key and the deployment's token stays home.
  2. **The endpoint must be allow-listed** (``VEXA_MODEL_BASE_URL_ALLOW``, comma-separated host
     globs). Unset, the default is the deployment's own configured gateway host(s) plus
     ``api.anthropic.com``, ``openrouter.ai`` and the CCC box. Loopback, link-local and private
     addresses — and single-label docker service names like ``redis`` — need an EXACT literal entry;
     a wildcard never reaches them, because ``*`` is a statement about the public internet and not
     a decision to expose the deployment's own network.

**"Is this a custom endpoint" was spelled three times and the spellings disagreed** — the dispatch
overlay's inertness rule, ``api._has_custom_model_endpoint``'s pre-flight gate, and
``config_test.run_models_test``'s mode sniff. The Test button certified a configuration the turn
would not use. There is now one function (`has_custom_endpoint`) and all three import it.
"""
from __future__ import annotations

import fnmatch
import ipaddress
import os
from typing import Mapping, Optional
from urllib.parse import urlsplit

#: The operator's gate. Comma-separated host globs (``fnmatch``: ``*.example.com``, ``vllm-*``).
ALLOW_ENV = "VEXA_MODEL_BASE_URL_ALLOW"

#: The default allow-list when the operator has set none: the two hosted gateways a custom endpoint
#: legitimately points at, and the CCC inference box this deployment's openai-agent lane runs on.
DEFAULT_ALLOW = ("api.anthropic.com", "openrouter.ai", "192.168.1.6")

#: The deployment's OWN endpoints, always allowed regardless of the operator list: a subject naming
#: the host this deployment already sends its credential to adds exactly zero exposure, and refusing
#: it would be a rule with no threat behind it.
_DEPLOYMENT_ENDPOINT_KEYS = ("ANTHROPIC_BASE_URL", "VEXA_LLM_BASE_URL")

_SCHEMES = ("http", "https")


def custom_base_url(config: Optional[dict]) -> str:
    """The subject's custom gateway URL, or ``""`` when this config delivers none.

    ``mode: custom`` WITHOUT a base_url is INERT — the deployment's own credentials still apply and
    nothing is overlaid — which is the rule ``overlay_model_config`` implements and the other two
    spellings of this question used to restate by hand."""
    cfg = config if isinstance(config, dict) else {}
    if (cfg.get("mode") or "").strip() != "custom":
        return ""
    return (cfg.get("base_url") or "").strip()


def has_custom_endpoint(config: Optional[dict]) -> bool:
    """True iff this Settings → Models config actually points the worker at another endpoint.

    THE one predicate: the dispatch overlay, ``api``'s credential pre-flight and the Test button all
    call this, so a config cannot be custom for one of them and subscription for another."""
    return bool(custom_base_url(config))


def host_of(url: str) -> str:
    """The lowercased hostname of ``url``, or ``""`` when it has none we can read."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return ""
    return (parts.hostname or "").lower()


def allowed_patterns(env: Optional[Mapping[str, str]] = None) -> list[str]:
    """The effective allow-list: the deployment's own gateway host(s) first (always), then either
    the operator's ``VEXA_MODEL_BASE_URL_ALLOW`` or ``DEFAULT_ALLOW``."""
    env = env if env is not None else os.environ
    out: list[str] = []
    for key in _DEPLOYMENT_ENDPOINT_KEYS:
        host = host_of(env.get(key) or "")
        if host and host not in out:
            out.append(host)
    raw = (env.get(ALLOW_ENV) or "").strip()
    configured = [p.strip().lower() for p in raw.split(",") if p.strip()] if raw else list(DEFAULT_ALLOW)
    for pattern in configured:
        if pattern not in out:
            out.append(pattern)
    return out


def _needs_literal(host: str) -> bool:
    """Hosts a wildcard must never reach: loopback, link-local (cloud metadata), private and
    reserved ranges, and single-label names — which is what every docker service on our own compose
    network is called (``redis``, ``admin-api``, ``runtime``)."""
    if host in ("localhost",) or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host          # a bare service name, not a public FQDN
    return bool(ip.is_loopback or ip.is_link_local or ip.is_private
                or ip.is_reserved or ip.is_unspecified or ip.is_multicast)


def refuse_reason(base_url: str, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """``None`` when this URL may be dispatched, else the operator-facing reason it may not.

    The message names the key to change, because a refusal nobody can act on is an outage with a
    log line."""
    raw = (base_url or "").strip()
    if not raw:
        return None                     # inert: no endpoint, nothing to gate
    try:
        parts = urlsplit(raw)
    except ValueError:
        return f"model base_url {raw!r} is not a URL"
    if parts.scheme.lower() not in _SCHEMES:
        return (f"model base_url {raw!r} is not http(s) — only {'/'.join(_SCHEMES)} endpoints may "
                "be dispatched")
    host = (parts.hostname or "").lower()
    if not host:
        return f"model base_url {raw!r} names no host"
    patterns = allowed_patterns(env)
    literal = host in patterns
    if not (literal or any(fnmatch.fnmatchcase(host, p) for p in patterns)):
        return (f"model endpoint host {host!r} is not allow-listed — add it to {ALLOW_ENV} "
                f"(currently: {', '.join(patterns)})")
    if _needs_literal(host) and not literal:
        return (f"model endpoint host {host!r} is loopback/private/internal and matched only a "
                f"wildcard — name it EXACTLY in {ALLOW_ENV} to allow it")
    return None


def refusal_friction(base_url: str, reason: str, *, subject: str = "") -> dict:
    """The friction record a refused endpoint files (PRD decision 33). A refusal the person cannot
    see is a turn that silently runs on the wrong model, which is the failure this whole gate is
    about."""
    return {
        "reporter": "agent",
        "kind": "refusal",
        "severity": "blocker",
        "subject": subject,
        "tried": "dispatch a workspace turn against the endpoint set in Settings → Models",
        "happened": reason,
        "would_help": (f"either allow-list the host in {ALLOW_ENV} on the deployment, or point "
                       "Settings → Models at an allowed endpoint"),
        "context": {"tool": "settings-models", "error": f"{reason} (base_url={base_url})"},
    }
