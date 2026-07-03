# webhooks — outbound delivery, system + per-client (O-MTG-2)

Outbound webhook delivery behind a **`WebhookSink`** port. Derived from the parent
`services/meeting-api/meeting_api/{webhook_delivery.py, webhook_url.py, webhook_retry_worker.py,
webhooks.py}`, reimplemented clean. The wire shape is sealed in `meetings/contracts/webhook.v1`.

## What it does
- **Envelope + HMAC** (`delivery.py`) — `build_envelope` = the `{event_id, event_type, api_version,
  created_at, data}` shape; `build_headers` signs `X-Webhook-Signature: sha256=<hmac(ts.payload)>`
  with the `X-Webhook-Timestamp` it used (replay window); `verify_signature` is the symmetric
  verifier a receiver runs (recompute HMAC over `ts.payload`, constant-time compare).
- **SSRF guard** (`ssrf.py`) — `validate_webhook_url` rejects localhost / loopback / link-local
  (incl. `169.254.169.254` cloud-metadata) / private CIDRs / internal Docker hostnames / non-http
  schemes, and resolves DNS names to catch rebinding. `resolver=` is injectable for offline evals.
- **Event filter** (`delivery.py`) — `is_event_enabled`: per-client subscribers only receive the
  events in their `webhook_events` map (default: `meeting.completed`). Suppressed before any HTTP.
- **Scopes** — `WebhookSink.deliver(..., scope=)`: `per-client` applies the filter; `system`
  (billing/analytics) bypasses it.
- **Retry** (`retry.py`) — a `RetryQueue` over a Redis list (`webhook:retry_queue`); a 5xx/429/
  transport-error enqueues; `drain_retry_queue` is one worker sweep (exponential `BACKOFF_SCHEDULE`
  = 1m·5m·30m·2h, 24h max-age). The eval drives the clock forward — no real sleeps.

The HTTP transport is **injected** (`transport(url, body, headers) -> resp`), so the eval supplies a
fake in-memory receiver — no httpx, no network, no live receiver.

## Evals
`tests/test_webhook_signing.py` · `test_webhook_delivery.py` · `test_webhook_ssrf.py`. Ride
`gate:python`. `webhook.v1` goldens conform via `gate:schema` (the contract is UNSEALED — sealing is
the human `lane:contract` step).
