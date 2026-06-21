# webhook.v1 — outbound delivery envelope + signed-header scheme

The **outbound webhook wire shape**: what the control-plane POSTs to a subscriber's URL, and how it
authenticates the delivery. Derived from the parent meeting-api's real envelope
(`webhook_delivery.build_envelope`) and header builder (`build_headers`). Both **system** hooks
(billing/analytics) and **per-client** hooks (user-configured `webhook_url` + `webhook_secret`) share
this shape.

> **UNSEALED** (in development). Sealing is the human `lane:contract` step (`pnpm seal:contracts`).
> Until then `gate:contract-version` reports it but does not fail.

## Shapes (`$defs`)
- **`Envelope`** — `event_id · event_type · api_version · created_at · data`. The body POSTed is
  `JSON.stringify(Envelope)`. `data` is event-type-specific (for `meeting.*`: `{ meeting, status_change? }`).
- **`EventType`** — the delivered event vocabulary (`meeting.started · meeting.status_change ·
  meeting.completed · bot.failed · recording.ready · transcription.ready`).
- **`SignatureHeaders`** — the headers a verifier recomputes. The signature is
  `sha256=<hmac_sha256(secret, "<X-Webhook-Timestamp>." + raw_body)>` — **timestamp-then-payload**,
  bounding replay. `Authorization: Bearer <secret>` rides alongside for legacy back-compat.

## Deliberately **not** in this contract
- **The secret never crosses the wire.** Only the HMAC of `ts.payload` does. Verification is symmetric:
  the receiver recomputes with its shared secret (ADR-0001 — data, not credentials).
- **Retry/backoff + SSRF policy are service-side**, not contract-side (they describe *delivery*, not the
  *message*). They live in `services/meeting-api/src/meeting_api/webhooks/`.

## Conformance
Goldens in [`golden/`](golden/) named `<Shape>.<case>.json`; `validate.mjs` (ajv) validates each against
its `$def` (the filename prefix). Run by `gate:schema`. The Python `webhooks/` brick re-derives the same
HMAC over `ts.payload` and its delivery eval asserts a verifier accepts the live signature.
