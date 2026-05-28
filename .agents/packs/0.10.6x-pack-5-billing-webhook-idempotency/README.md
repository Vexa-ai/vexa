# Pack 5 — Billing & Webhook Idempotency (scope clarification)

Pack id: `0.10.6x-pack-5-billing-webhook-idempotency`
Epic: https://github.com/Vexa-ai/vexa/issues/360
Pack PR: https://github.com/Vexa-ai/vexa/pull/369
Release: `0.10.6.x` (stitched into `0.10.6.3`)

This README documents what Pack 5 actually guarantees end-to-end, since the
pack name ("Billing And Webhook Idempotency") is misleading about scope.
The shipped code is generic; the word "billing" is a deployment label, not
a property of the mechanism.

## What Pack 5 ships (sender-side, exactly-once dispatch)

Pack 5 adds a producer-side idempotency ledger for outbound post-meeting
events fired by `services/meeting-api`. The contract is:

- For every `(meeting_id, channel, event_type, destination)` tuple,
  meeting-api emits **at most one** delivery attempt per claim.
- The claim is taken under a row lock (`SELECT … FOR UPDATE`) against the
  `meeting.data.outbound_events` ledger. A duplicate claim returns
  `should_deliver=False` and the `attempts` counter is **not** incremented.
- `event_id` is **stable across all retries** of the same logical event
  — it is derived deterministically from
  `channel:event_type:meeting_id:sha256(destination)[:16]`
  (see `outbound_events.event_key`). Receivers can therefore dedupe on
  `event_id` across the network retry surface.
- Stuck `pending` claims older than the configured max-age are swept and
  re-queued; this preserves the "at-most-one delivery per claim" property
  without permanently stranding events on a crash between claim and
  HTTP send.

Verified during stitch validation of `v0.10.6.3`: `claim_outbound_event`
row-lock idempotency works — duplicate claim returns
`should_deliver=False`, `attempts` unchanged.

## What Pack 5 does NOT ship (receiver-side dedupe)

End-to-end exactly-once **charging** requires the receiver to dedupe on
`event_id` as well. Pack 5 only owns the sender-side half of the
contract:

| Layer                                | Owned by Pack 5? |
| ------------------------------------ | ---------------- |
| Claim under row lock                 | yes              |
| Stable `event_id` across retries     | yes              |
| Pending-sweep recovery               | yes              |
| HTTP transport retry / queue         | yes              |
| Receiver-side dedupe on `event_id`   | **no** (receiver contract) |
| Receiver's billing ledger commit     | **no** (receiver contract) |

If a downstream service mounted at a `POST_MEETING_HOOKS` URL processes
the same `event_id` twice (e.g. because it ack'd after a crash without
persisting the dedupe row), end-to-end exactly-once is broken at the
receiver, not at Pack 5.

## "Billing" is the deployment label, not the mechanism

The mechanism is a **generic** exactly-once delivery primitive for any
URL listed in the `POST_MEETING_HOOKS` env var (see
`services/meeting-api/meeting_api/config.py` and
`docker-compose.yml`).

- **OSS default** (this repo, `deploy/compose/docker-compose.yml:265`):
  ```
  POST_MEETING_HOOKS=${POST_MEETING_HOOKS:-http://agent-api:8100/internal/webhooks/meeting-completed}
  ```
  The only configured destination is `agent-api`, used for chat-state
  propagation. There is **no billing service in OSS**. The exactly-once
  guarantee still applies, but it benefits chat-state propagation, not
  billing.
- **SaaS / production deployments**: operators add their billing-service
  URL(s) to `POST_MEETING_HOOKS`. They benefit from the same sender-side
  exactly-once guarantee, provided the receiver implements `event_id`
  dedupe.

The pack name "Billing And Webhook Idempotency" was chosen for the
CEO/CTO/user outcome framing on the epic. The shipped code carries no
payment-processor, plan-tier, or account-credit semantics — it is a
generic at-most-once-claim primitive plus durable retry plumbing.

## Cross-references

- Files shipped in PR #369 (see `gh pr diff 369`):
  - `services/meeting-api/meeting_api/outbound_events.py` (ledger + claim)
  - `services/meeting-api/meeting_api/post_meeting.py` (call site)
  - `services/meeting-api/meeting_api/webhook_delivery.py` (transport)
  - `services/meeting-api/meeting_api/webhook_retry_worker.py` (sweep)
  - `services/meeting-api/meeting_api/webhooks.py` (public webhook
    payloads unchanged in this pack)
  - `services/meeting-api/meeting_api/dispatch_check.py` (separate
    dispatch-time gate; not part of the outbound-event ledger contract,
    but ships in the same PR)
  - `services/meeting-api/tests/test_post_meeting_idempotency.py`

- Code-review squash commit (product-only, 6 files, 881 lines):
  https://github.com/Vexa-ai/vexa/commit/5cb881d3b6fcacbb0a277ce74a7bf4616ddfd04d

- Stitched release candidate: `0.10.6.3`.
