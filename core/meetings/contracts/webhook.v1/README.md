# webhook.v1 — outbound delivery envelope + signed-header scheme

The **outbound webhook wire shape**: what the control-plane POSTs to a subscriber's URL, and how it
authenticates the delivery. Derived from the parent meeting-api's real envelope
(`webhook_delivery.build_envelope`) and header builder (`build_headers`). Both **system** hooks
(billing/analytics) and **per-client** hooks (user-configured `webhook_url` + `webhook_secret`) share
this shape.

> **SEALED** — pinned in `contracts.seal.json`; changes ride the human `lane:contract` review
> (`pnpm seal:contracts` re-pins the hash).

## Delivery semantics (#519)
- **At-least-once.** A logical event may be POSTed more than once — the initial send, a
  retry-queue drain, a restart replay, or a cross-replica race can all re-emit it.
- **`event_id` is the receiver's idempotency key.** The SAME logical event carries the SAME
  `event_id` across every (re)delivery — it is derived from what makes the event unique
  (`connection_id · event_type · new_status`), not minted fresh per send. **Receivers MUST dedupe
  on `event_id`** and process each logical event once. (This closes the #330 4×-billing class,
  where a per-emission `uuid4` made redeliveries look like distinct events.)
- **Do NOT key on the body or the signature.** `created_at` and the `X-Webhook-Timestamp` (hence the
  HMAC signature) legitimately differ across redeliveries — that is the replay-bounding design, not
  a new event. Only `event_id` is stable.
- **Two events per FSM advance.** One advance (e.g. → `active`) emits both `meeting.status_change`
  and the typed `meeting.started`; these are DISTINCT logical events with DISTINCT `event_id`s
  (`event_type` is part of the identity).
- **Retention.** Retain seen `event_id`s ≥ 48h (the retry schedule tops out at 24h, `retry.py`, plus
  slack) to dedupe a late redelivery.

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
