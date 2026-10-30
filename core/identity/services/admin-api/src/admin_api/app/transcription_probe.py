"""Judge a person's saved STT endpoint with the SAME oracle the deployment's own backend gets.

``config_preflight._http_probe`` (a real ~1s audio round-trip) decides ``unauthorized`` /
``exhausted`` / ``invalid_endpoint`` / ``unreachable``. The spec below mirrors meeting-api's ``stt``
probe in ``config.v1.json``. The probe's own ``reason`` embeds the URL or exception text, so it is
DISCARDED: callers get only the kind, the status and a fixed sentence — never the URL or token.
"""
from __future__ import annotations

from .. import config_preflight as cp

STT_PROBE_SPEC = {
    "method": "POST", "url_key": "TRANSCRIPTION_SERVICE_URL", "path": "/v1/audio/transcriptions",
    "auth_key": "TRANSCRIPTION_SERVICE_TOKEN", "unauthorized_statuses": [401, 403],
    "invalid_statuses": [404], "exhausted_statuses": [402], "payload": "audio",
}
PROBE_TIMEOUT_S = 15.0

REASONS = {
    "unauthorized": "the endpoint rejected the token (missing or wrong)",
    "exhausted": "the token is valid but the account cannot pay for transcription",
    "invalid_endpoint": "the endpoint path was not found; check the URL",
    "unreachable": "the endpoint could not be reached",
}


def probe_endpoint(url: str, token: str) -> dict:
    """Blocking. ``{"ok": True}`` or ``{"ok": False, "kind", "status"?, "reason"}`` (fixed reason)."""
    env = {"TRANSCRIPTION_SERVICE_URL": url, "TRANSCRIPTION_SERVICE_TOKEN": token or ""}
    verdict = cp._http_probe(STT_PROBE_SPEC, env, PROBE_TIMEOUT_S)
    if verdict.get("ok"):
        return {"ok": True}
    kind = verdict.get("kind") or "unreachable"
    out = {"ok": False, "kind": kind, "reason": REASONS.get(kind, REASONS["unreachable"])}
    if verdict.get("status") is not None:
        out["status"] = verdict["status"]
    return out


def is_config_fault(kind: str) -> bool:
    return kind in cp.CONFIG_FAULT_KINDS
