"""Judge a person's SAVED STT endpoint before a spawn uses it (#1924 s2).

The question is the one the deployment's own backend is asked (``config_preflight``: the declared
``stt`` probe — a ~1 s audio POST, 401/403 ``unauthorized``, 402 ``exhausted``, 404
``invalid_endpoint``, a network failure ``unreachable``, anything else ok), sent through the outbound
URL guard: every address the host resolves to is checked, the connection is pinned to a checked
address, redirects are not followed. A host that does not resolve is ``unreachable``; any other guard
refusal is ``internal_address``. Callers get ``{ok, kind, status}`` only — never the URL, the token
or exception text. Verdicts are cached under a hash of (url, token): ok/unreachable for the declared
``ttl_s``, a fault for 60 s so an endpoint fixed at the provider heals quickly.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Dict, Optional, Tuple

import httpx

from .. import config_preflight as cp
from ..webhooks import ssrf

FAULT_KINDS = frozenset(cp.CONFIG_FAULT_KINDS) | {"internal_address"}
FAULT_TTL_S = 60.0
_MAX_TIMEOUT_S = 10.0  # bot-context 5 s + this + the spawn stays under the gateway's 30 s hop

REASONS = {
    "unauthorized": "the endpoint rejected the token (missing or wrong)",
    "exhausted": "the token is valid but the account cannot pay for transcription",
    "invalid_endpoint": "the endpoint path was not found; check the URL",
    "internal_address": "the endpoint resolves to an internal or private address",
    "unreachable": "the endpoint could not be reached",
}

# httpx logs the request line (the person's URL) at INFO; keep it out of logs.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

resolver = None  # DNS seam (tests); None = the system resolver
_now = time.monotonic  # clock seam (tests)
_cache: Dict[str, Tuple[float, float, dict]] = {}  # key-hash -> (at, ttl, verdict)


def _spec() -> Tuple[dict, float, float]:
    probe = cp.load_declaration()["capabilities"]["stt"]["probe"]
    return probe["http"], min(float(probe.get("timeout_s", 15)), _MAX_TIMEOUT_S), float(probe.get("ttl_s", 900))


def probe_endpoint(url: str, token: str) -> dict:
    """Blocking, never raises. ``{"ok", "kind", "status"}``."""
    http, timeout, _ = _spec()
    try:
        ssrf.validate_url(url, resolver, what="url", resolve=True)
    except ssrf.SSRFError as exc:
        kind = "unreachable" if "could not be resolved" in str(exc) else "internal_address"
        return {"ok": False, "kind": kind, "status": None}
    content_type, body = cp.audio_probe_body("whisper-1")
    headers = {"Content-Type": content_type}
    if (token or "").strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    try:
        transport = ssrf.build_pinned_sync_transport(resolver=resolver)
        with httpx.Client(transport=transport, follow_redirects=False, timeout=timeout) as client:
            status = client.post(cp.probe_url(url, http["path"]), content=body, headers=headers).status_code
    except ssrf.SSRFError:
        return {"ok": False, "kind": "internal_address", "status": None}
    except Exception:  # noqa: BLE001 — the text may carry the URL; discarded
        return {"ok": False, "kind": "unreachable", "status": None}
    for kind, key in (("unauthorized", "unauthorized_statuses"), ("exhausted", "exhausted_statuses"),
                      ("invalid_endpoint", "invalid_statuses")):
        if status in http.get(key, ()):
            return {"ok": False, "kind": kind, "status": status}
    return {"ok": True, "kind": None, "status": status}


_probe = probe_endpoint  # the one function that dials; tests/conftest.py stubs it


def _key(url: str, token: str) -> str:
    return hashlib.sha256(f"{url}\0{token}".encode()).hexdigest()


def reset_cache() -> None:
    _cache.clear()


async def verdict(url: str, token: str) -> dict:
    """The cached verdict for (url, token), probing at most once per pair per TTL."""
    key = _key(url, token or "")
    hit = _cache.get(key)
    if hit is not None and _now() - hit[0] < hit[1]:
        return hit[2]
    result = await asyncio.to_thread(_probe, url, token or "")
    _, _, ok_ttl = _spec()
    ttl = FAULT_TTL_S if result.get("kind") in FAULT_KINDS else ok_ttl
    _cache[key] = (_now(), ttl, {"ok": bool(result.get("ok")), "kind": result.get("kind"),
                                 "status": result.get("status")})
    return _cache[key][2]


def reason(kind: Optional[str]) -> str:
    return REASONS.get(kind or "", "the endpoint failed its check")
