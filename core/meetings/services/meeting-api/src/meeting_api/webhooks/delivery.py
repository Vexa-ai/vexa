"""Envelope + HMAC signing + the WebhookSink delivery port.

Derived from the parent's `webhook_delivery.py` + `webhooks.py`, reimplemented clean.
The wire shape is sealed in `meetings/contracts/webhook.v1`.

The HMAC scheme is the parent's exactly: `sha256=<hmac_sha256(secret, "<ts>." + body)>`
in `X-Webhook-Signature`, with the unix-seconds `X-Webhook-Timestamp` it was computed
at (replay window). `verify_signature` is the symmetric verifier a receiver runs.

Delivery is transport-injected: the `WebhookSink` takes an async `transport(url, body,
headers) -> Response` so the eval supplies a fake in-memory receiver (no httpx, no
network). On a 2xx the delivery is `delivered`; on a 5xx/timeout it is enqueued to the
`RetryQueue` for the worker sweep to drain.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional
from uuid import uuid4

from .ssrf import SSRFError, validate_webhook_url

# Frozen wire constants (match webhook.v1 + the parent).
WEBHOOK_API_VERSION = "2026-03-01"

# Per-client default-enabled events when the subscriber didn't configure a filter.
_DEFAULT_ENABLED = frozenset({"meeting.completed"})

# Internal meeting.data keys stripped before delivery (parent's _INTERNAL_DATA_KEYS).
_INTERNAL_DATA_KEYS = frozenset({
    "webhook_delivery", "webhook_deliveries", "webhook_secret", "webhook_secrets",
    "webhook_events", "webhook_url", "outbound_events",
    "bot_container_id", "container_name",
})


def build_envelope(event_type: str, data: Dict[str, Any], event_id: Optional[str] = None) -> Dict[str, Any]:
    """Build the standardized webhook.v1 Envelope.

    `{event_id, event_type, api_version, created_at, data}` — the only shape delivered,
    system or per-client.

    #519: ``event_id`` is the STABLE identity of a logical event (the receiver's idempotency key),
    NOT a per-emission nonce. Callers MUST pass a deterministic ``event_id`` derived from what makes
    the event unique (see ``lifecycle/webhook.derive_event_id``) so redeliveries dedupe. The
    ``uuid4`` fallback here is a last resort for a caller that has no stable identity to offer; there
    is no such production caller in-tree today.
    """
    return {
        "event_id": event_id or f"evt_{uuid4().hex}",
        "event_type": event_type,
        "api_version": WEBHOOK_API_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def clean_meeting_data(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Strip internal keys from meeting.data before it ships in a payload."""
    if not data:
        return {}
    return {k: v for k, v in data.items() if k not in _INTERNAL_DATA_KEYS}


def sign_payload(payload_bytes: bytes, secret: str, timestamp: str) -> str:
    """`sha256=<hmac_sha256(secret, "<timestamp>." + payload_bytes)>` — the wire signature."""
    signed_content = f"{timestamp}.".encode() + payload_bytes
    sig = hmac.new(secret.encode(), signed_content, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def build_headers(
    webhook_secret: Optional[str] = None,
    payload_bytes: Optional[bytes] = None,
    timestamp: Optional[str] = None,
) -> Dict[str, str]:
    """Build the delivery headers (webhook.v1 SignatureHeaders when a secret is set).

    - `Content-Type: application/json` always.
    - With a secret + payload: `Authorization: Bearer <secret>` (legacy back-compat),
      `X-Webhook-Signature: sha256=<hmac(ts.payload)>`, `X-Webhook-Timestamp: <ts>`.
    """
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if webhook_secret and webhook_secret.strip():
        secret = webhook_secret.strip()
        headers["Authorization"] = f"Bearer {secret}"
        if payload_bytes is not None:
            ts = timestamp or str(int(time.time()))
            headers["X-Webhook-Signature"] = sign_payload(payload_bytes, secret, ts)
            headers["X-Webhook-Timestamp"] = ts
    return headers


def verify_signature(payload_bytes: bytes, headers: Dict[str, str], secret: str) -> bool:
    """The symmetric verifier a receiver runs: recompute HMAC over `ts.payload`.

    Returns True iff `X-Webhook-Signature` matches `sha256=<hmac(ts.payload)>` for the
    delivered `X-Webhook-Timestamp` and the shared `secret`. Constant-time compare.
    """
    sig = headers.get("X-Webhook-Signature")
    ts = headers.get("X-Webhook-Timestamp")
    if not sig or not ts:
        return False
    expected = sign_payload(payload_bytes, secret, ts)
    return hmac.compare_digest(sig, expected)


def is_event_enabled(events_config: Optional[Dict[str, Any]], event_type: str) -> bool:
    """Per-client event filter (parent's `_is_event_enabled`).

    `events_config` is the subscriber's `webhook_events` map (`{event_type: bool}`).
    When absent/empty, only `_DEFAULT_ENABLED` events fire. An explicit per-event flag
    wins; otherwise fall back to the default set.
    """
    if not events_config or not isinstance(events_config, dict):
        return event_type in _DEFAULT_ENABLED
    enabled = events_config.get(event_type)
    if enabled is not None:
        return bool(enabled)
    return event_type in _DEFAULT_ENABLED


@dataclass(frozen=True)
class DeliveryResult:
    """Structured delivery outcome (parent's DeliveryResult)."""

    status: str  # "delivered" | "queued" | "suppressed" | "blocked" | "failed"
    status_code: Optional[int] = None
    queued: bool = False
    error: Optional[str] = None


# An async transport: POST the body+headers to the URL, return an object with a
# `.status_code`. The eval supplies a fake in-memory receiver; production wires httpx.
Transport = Callable[[str, bytes, Dict[str, str]], Awaitable[Any]]


class WebhookSink:
    """The port: build → SSRF-guard → event-filter → deliver → enqueue-on-failure.

    Scopes:
    * **system** — the platform's own hooks (billing/analytics). No event-filter; the
      caller decides what to send. Pass `events_config=None` + `scope="system"`.
    * **per-client** — a user-configured `webhook_url`/`webhook_secret`/`webhook_events`.
      The event-filter suppresses unsubscribed events before any HTTP happens.

    `transport` is injected (fake receiver in the eval). `queue` is the `RetryQueue`
    (fakeredis-backed); a 5xx/timeout enqueues the delivery for the worker sweep.
    """

    def __init__(self, transport: Transport, queue: "Any" = None, resolver: Callable | None = None):
        self.transport = transport
        self.queue = queue
        self._resolver = resolver

    async def deliver(
        self,
        url: str,
        envelope: Dict[str, Any],
        webhook_secret: Optional[str] = None,
        *,
        scope: str = "per-client",
        events_config: Optional[Dict[str, Any]] = None,
        label: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeliveryResult:
        event_type = envelope.get("event_type", "")

        # 1. Per-client event filter — suppress unsubscribed events before any HTTP.
        if scope == "per-client" and not is_event_enabled(events_config, event_type):
            return DeliveryResult(status="suppressed")

        # 2. SSRF guard — reject private/internal targets before any HTTP.
        # System-scope targets are OPERATOR config (env, e.g. the hosted platform's in-cluster
        # billing hook), not user input — cluster-internal URLs are the point there, so the
        # guard (which stops user-supplied webhook_url reaching internal networks) is skipped.
        if scope != "system":
            try:
                validate_webhook_url(url, resolver=self._resolver)
            except SSRFError as e:
                return DeliveryResult(status="blocked", error=str(e))

        payload_bytes = json.dumps(envelope).encode()
        ts = str(int(time.time()))
        headers = build_headers(webhook_secret, payload_bytes, timestamp=ts)

        # 3. Deliver. 2xx → delivered. 5xx/429/transport-error → enqueue for retry.
        try:
            resp = await self.transport(url, payload_bytes, headers)
            code = getattr(resp, "status_code", 0)
            if code < 300:
                return DeliveryResult(status="delivered", status_code=code)
            if code >= 500 or code == 429:
                raise _RetryableStatus(code)
            # 4xx (except 429) — permanent failure, do not retry.
            return DeliveryResult(status="failed", status_code=code, error=f"HTTP {code}")
        except Exception as e:  # noqa: BLE001 — any transport error is retryable
            code = e.code if isinstance(e, _RetryableStatus) else None
            if self.queue is not None:
                await self.queue.enqueue(
                    url=url, envelope=envelope, webhook_secret=webhook_secret,
                    label=label, metadata=metadata,
                )
                return DeliveryResult(status="queued", status_code=code, queued=True, error=str(e))
            return DeliveryResult(status="failed", status_code=code, error=str(e))


class _RetryableStatus(Exception):
    """Internal marker: a 5xx/429 that should route to the retry queue."""

    def __init__(self, code: int):
        self.code = code
        super().__init__(f"retryable status {code}")
