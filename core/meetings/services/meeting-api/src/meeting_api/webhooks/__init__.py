"""webhooks — outbound webhook delivery (system + per-client) behind a WebhookSink port.

Front door (P6): import from here, never a deep module path.

Derived from the parent meeting-api's `webhook_delivery.py` / `webhook_url.py` /
`webhook_retry_worker.py` / `webhooks.py`, reimplemented clean. The wire shape is sealed
in `meetings/contracts/webhook.v1`.

* ``build_envelope`` / ``sign_payload`` / ``build_headers`` / ``verify_signature`` —
  the envelope + HMAC-over-`ts.payload` scheme (and its verifier).
* ``validate_webhook_url`` / ``SSRFError`` — the SSRF URL-guard (localhost/link-local/
  private CIDRs + internal hostnames).
* ``is_event_enabled`` — the per-client event filter (subscribed events in user.data).
* ``WebhookSink`` — the port: build → SSRF-guard → filter → deliver → enqueue-on-failure.
* ``RetryQueue`` — the fakeredis-backed exponential-backoff retry queue.
* ``drain_retry_queue`` — the retry-worker sweep (the worker loop's one tick).
* ``WEBHOOK_API_VERSION`` / ``RETRY_QUEUE_KEY`` / ``BACKOFF_SCHEDULE`` — frozen constants.
"""
from .delivery import (
    WEBHOOK_API_VERSION,
    DeliveryResult,
    WebhookSink,
    build_envelope,
    build_headers,
    clean_meeting_data,
    is_event_enabled,
    sign_payload,
    verify_signature,
)
from .retry import (
    BACKOFF_SCHEDULE,
    MAX_AGE_SECONDS,
    RETRY_QUEUE_KEY,
    RetryQueue,
    drain_retry_queue,
)
from .ssrf import SSRFError, validate_webhook_url

__all__ = [
    "WEBHOOK_API_VERSION",
    "DeliveryResult",
    "WebhookSink",
    "build_envelope",
    "build_headers",
    "clean_meeting_data",
    "is_event_enabled",
    "sign_payload",
    "verify_signature",
    "BACKOFF_SCHEDULE",
    "MAX_AGE_SECONDS",
    "RETRY_QUEUE_KEY",
    "RetryQueue",
    "drain_retry_queue",
    "SSRFError",
    "validate_webhook_url",
]
