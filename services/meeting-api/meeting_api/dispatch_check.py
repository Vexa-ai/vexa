"""Generic env-gated dispatch-check call-out.

When ``DISPATCH_CHECK_URL`` is set, bot-create / dispatch entry points call
out to an external authority to ask "should this action proceed?" The
authority returns ``allow`` / ``deny`` with an optional reason. When the
env var is unset (default for OSS self-hosters), the helper is a no-op
and dispatch proceeds unchanged.

This module is intentionally generic: it carries no payment-processor,
account-credit, usage-cap, plan-tier, account-identifier, or recurring-charge
semantics. OSS just asks an opaque authority an opaque question
and gates on the response. The authority (typically a vexa-platform
endpoint when env var is set; nothing when unset) decides why.

Failure policy: fail-OPEN on network error, timeout, parse error, or
5xx response. Rationale: we'd rather risk an over-charge than block
paying customers on transient gate failure. The authority side is
expected to log gate-call-misses for reconciliation.

Env vars:
    DISPATCH_CHECK_URL    Optional. Base URL of the authority. When
                          unset, all checks succeed unconditionally
                          (the OSS-self-hoster default).
    DISPATCH_CHECK_SECRET Optional. Shared secret. When set, the outbound
                          request carries the HMAC-signed timestamped header
                          set produced by ``webhook_delivery.build_headers``:
                          ``Authorization: Bearer <secret>`` +
                          ``X-Webhook-Timestamp`` +
                          ``X-Webhook-Signature: sha256=<hmac>`` over
                          ``<ts>.<body>``. Matches the inbound webhook
                          verifier on the webapp side and closes the
                          bearer-replay window.
    DISPATCH_CHECK_TIMEOUT_S
                          Optional. Per-call timeout in seconds (float).
                          Default: ``2.0``.

Behavior matrix:

    | DISPATCH_CHECK_URL | Authority response   | Result            |
    | ------------------ | -------------------- | ----------------- |
    | unset              | (not called)         | allow (no-op)     |
    | set                | 200 ``{"allow":true}``     | allow       |
    | set                | 200 ``{"allow":false,"reason":"x"}`` | deny |
    | set                | 5xx                  | allow (fail-open) |
    | set                | network error/timeout | allow (fail-open)|
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import httpx

from .webhook_delivery import build_headers

logger = logging.getLogger("meeting_api.dispatch_check")


def _env_url() -> Optional[str]:
    """Read the URL each call so tests / hot-reload pick up changes."""
    url = os.environ.get("DISPATCH_CHECK_URL", "").strip()
    return url or None


def _env_secret() -> str:
    return os.environ.get("DISPATCH_CHECK_SECRET", "").strip()


def _env_timeout() -> float:
    try:
        return float(os.environ.get("DISPATCH_CHECK_TIMEOUT_S", "2.0"))
    except (TypeError, ValueError):
        return 2.0


@dataclass
class DispatchCheckResult:
    """Outcome of a single dispatch-check call.

    ``allow``       True if the action is permitted (or no authority
                    configured, or authority unreachable — fail-open).
    ``reason``      Optional opaque string returned by the authority
                    explaining a deny. Forwarded to the client.
    ``http_status`` Status code observed (or sentinel: 0 if not called,
                    599 if network/timeout).
    """

    allow: bool
    reason: Optional[str] = None
    http_status: int = 200


async def dispatch_check(
    *,
    user_id: Any,
    action: str,
    context: Optional[Mapping[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> DispatchCheckResult:
    """Ask the configured authority whether ``user_id`` may perform ``action``.

    Returns ``DispatchCheckResult(allow=True)`` when ``DISPATCH_CHECK_URL`` is
    unset — the no-op default for OSS self-hosters. Otherwise calls the
    configured authority and returns its decision.

    Auth scheme: when ``DISPATCH_CHECK_SECRET`` is set, the outbound request
    carries the same HMAC-signed timestamped headers produced by
    :func:`webhook_delivery.build_headers` — ``Authorization: Bearer <secret>``
    plus ``X-Webhook-Timestamp`` plus ``X-Webhook-Signature: sha256=<hmac>``
    over ``<timestamp>.<body>``. This matches the inbound webhook verifier
    shape (``verifyInternalWebhook`` on the webapp side) and closes the
    bearer-replay window. The body bytes signed are EXACTLY the bytes put
    on the wire (computed once, then passed via ``content=`` so httpx
    re-serialization can't drift).

    Fail-open on:
      * 5xx response
      * Network error / timeout
      * JSON parse error / unexpected body shape

    The fail-open policy is intentional: blocking paying customers on a
    transient gate-authority outage is a worse outcome than letting a
    small number of disallowed actions through. The authority side is
    responsible for logging gate-call-misses and reconciling later.

    Parameters
    ----------
    user_id : any
        Identifier the authority uses to scope the decision. Passed
        through as-is; the authority decides the shape.
    action : str
        Opaque action name. Conventional values: ``"create-bot"``,
        ``"admin-create-bot"``, etc. The authority is the source of
        truth for which strings are recognised.
    context : optional mapping
        Optional extra context forwarded to the authority. Keep it
        small — this is sent on every dispatch.
    client : optional httpx.AsyncClient
        Caller-supplied client (e.g. the shared app-state client).
        When omitted a per-call client is created.
    """

    url = _env_url()
    if not url:
        # No authority configured — OSS self-host default. Allow.
        return DispatchCheckResult(allow=True, http_status=0)

    payload: dict = {"user_id": user_id, "action": action}
    if context:
        payload["context"] = dict(context)

    # Serialize FIRST, then sign — the signed bytes must be identical to the
    # bytes that go on the wire. Use sort_keys + compact separators so the
    # same payload produces a byte-identical body across runs (deterministic
    # for any future debug-replay).
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    secret = _env_secret()
    # build_headers handles the no-secret case (Bearer/HMAC omitted) and
    # the with-secret case (Bearer + X-Webhook-Timestamp + X-Webhook-Signature).
    headers = build_headers(secret or None, body_bytes if secret else None)

    timeout = _env_timeout()
    endpoint = f"{url.rstrip('/')}/check"

    try:
        if client is not None:
            resp = await client.post(endpoint, content=body_bytes, headers=headers, timeout=timeout)
        else:
            async with httpx.AsyncClient(timeout=timeout) as c:
                resp = await c.post(endpoint, content=body_bytes, headers=headers)

        if resp.status_code >= 500:
            logger.warning(
                "dispatch_check authority returned %s — failing open (action=%s user_id=%s)",
                resp.status_code, action, user_id,
            )
            return DispatchCheckResult(allow=True, http_status=resp.status_code)

        try:
            body = resp.json() or {}
        except ValueError:
            logger.warning(
                "dispatch_check authority returned non-JSON body — failing open "
                "(status=%s action=%s user_id=%s)",
                resp.status_code, action, user_id,
            )
            return DispatchCheckResult(allow=True, http_status=resp.status_code)

        # Default to allow on missing key — fail-open on malformed.
        allow = bool(body.get("allow", True))
        reason = body.get("reason")
        if isinstance(reason, str) and reason:
            reason_out: Optional[str] = reason
        else:
            reason_out = None
        return DispatchCheckResult(allow=allow, reason=reason_out, http_status=resp.status_code)

    except Exception as e:  # noqa: BLE001 — fail-open on ANY transport issue
        logger.warning(
            "dispatch_check transport failure — failing open (action=%s user_id=%s err=%s)",
            action, user_id, e,
        )
        return DispatchCheckResult(allow=True, http_status=599)
