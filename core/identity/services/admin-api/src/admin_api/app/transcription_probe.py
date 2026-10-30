"""Judge a person's saved STT endpoint with the SAME oracle the deployment's own backend gets.

The request is the one ``config_preflight._http_probe`` makes (a real ~1s audio round-trip) and the
classification is the same oracle: 401/403 ``unauthorized``, 402 ``exhausted``, 404
``invalid_endpoint``, a network failure ``unreachable``, anything else ok. It is sent through the
outbound URL guard (``ssrf``): every address the host resolves to is checked, the connection is
pinned to a checked address at connect time, and redirects are not followed — so nothing internal is
ever dialled on a person's say-so. Callers get only the kind, the status and a fixed sentence —
never the URL, the token or the exception text.
"""
from __future__ import annotations

import logging

import httpx

from .. import config_preflight as cp
from . import ssrf

# Mirrors the request/oracle fields of meeting-api's `stt` probe in its config.v1.json (admin-api's
# own declaration has no stt capability); tests/test_transcription_save_probe.py fails if they drift
# apart. The spec's env-key fields are not mirrored: the URL and token are the person's own, passed
# in, so this module never names meeting-api's env keys.
STT_PROBE_SPEC = {
    "method": "POST", "path": "/v1/audio/transcriptions",
    "unauthorized_statuses": [401, 403],
    "invalid_statuses": [404], "exhausted_statuses": [402], "payload": "audio",
}
PROBE_TIMEOUT_S = 15.0

# httpx logs every request line (the person's URL) at INFO and httpcore traces at DEBUG; the URL
# must stay out of logs, so those loggers speak only for problems.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

REASONS = {
    "unauthorized": "the endpoint rejected the token (missing or wrong)",
    "exhausted": "the token is valid but the account cannot pay for transcription",
    "invalid_endpoint": "the endpoint path was not found; check the URL",
    "unreachable": "the endpoint could not be reached",
}


def _status(url: str, token: str, resolver=None) -> int:
    """POST the probe audio and return the HTTP status, through the pinned transport, no redirects."""
    content_type, body = cp.audio_probe_body(STT_PROBE_SPEC.get("payload_model") or "whisper-1")
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    transport = ssrf.build_pinned_sync_transport(resolver=resolver)
    with httpx.Client(transport=transport, follow_redirects=False, timeout=PROBE_TIMEOUT_S) as client:
        return client.post(cp.probe_url(url, STT_PROBE_SPEC["path"]), content=body, headers=headers).status_code


def probe_endpoint(url: str, token: str, resolver=None) -> dict:
    """Blocking. ``{"ok": True}`` or ``{"ok": False, "kind", "status"?, "reason"}`` (fixed reason).

    Raises ``ssrf.SSRFError`` when the URL, or any address its host resolves to, is internal: it is
    refused before any connection is made."""
    try:
        ssrf.validate_url(url, resolver, what="url", resolve=True)
    except ssrf.SSRFError as exc:
        if "could not be resolved" not in str(exc):
            raise
        return {"ok": False, "kind": "unreachable", "reason": REASONS["unreachable"]}
    try:
        status = _status(url, (token or "").strip(), resolver)
    except ssrf.SSRFError:
        raise
    except Exception:  # noqa: BLE001 — a probe must never throw past here; the text is discarded
        return {"ok": False, "kind": "unreachable", "reason": REASONS["unreachable"]}
    kind = None
    if status in STT_PROBE_SPEC["unauthorized_statuses"]:
        kind = "unauthorized"
    elif status in STT_PROBE_SPEC["exhausted_statuses"]:
        kind = "exhausted"
    elif status in STT_PROBE_SPEC["invalid_statuses"]:
        kind = "invalid_endpoint"
    if kind is None:
        return {"ok": True}
    return {"ok": False, "kind": kind, "status": status, "reason": REASONS[kind]}


def is_config_fault(kind: str) -> bool:
    return kind in cp.CONFIG_FAULT_KINDS
