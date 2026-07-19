# zaki-control.v1 — Minutes control plane and callback profile

The versioned service boundary between the ZAKI Hub BFF and the Minutes engine. It covers
owner-scoped policy provisioning, capture lifecycle, idempotent metering, signed callbacks and
content-free Minutes erasure receipts. It does not grant the browser direct engine access and does
not activate Minutes.

> **SEALED PROFILE.** `zaki-control.schema.json`, the positive/negative vectors under `golden/`,
> this HTTP profile and the contract seal move together. A breaking change adds `zaki-control.v2`;
> a backward-compatible change requires a new `lane:contract` review and re-seal.

## Trust boundary

The authenticated Hub BFF is the sole caller and credential holder. Browser routes may adapt these
shapes, but the browser receives no engine base URL, service token, webhook key, provider/admin
credential, storage key, database setting or native meeting-engine credential.

Every control request is authorized by the service token and repeats one canonical identity in all
four places: token scope, `{userId}` path, `X-Zaki-User-Id` and `subject.user_id`. The tenant scope in
the token must also match `X-Zaki-Tenant-Id` and `subject.tenant_id`. Any mismatch fails closed as
`subject_mismatch`; a caller must not learn whether the foreign resource exists. Redirects are
forbidden and responses use `Cache-Control: no-store`.

## HTTP profile

```text
POST /api/zaki/control/v1/{userId}/ensure
POST /api/zaki/control/v1/{userId}/captures
GET  /api/zaki/control/v1/{userId}/captures/{captureId}
POST /api/zaki/control/v1/{userId}/captures/{captureId}/stop
POST /api/zaki/control/v1/{userId}/meetings/{meetingId}/erase
POST /api/zaki/control/v1/{userId}/erase

POST /api/minutes/callback/v1                 # engine → Hub BFF
```

Mutations require `X-Request-Id` and `Idempotency-Key`; their values must equal `request_id` and
`idempotency_key` in the body. The idempotency namespace is `(api_version, tenant_id, user_id,
operation, idempotency_key)`, where `operation` is one of `ensure`, `capture`, `stop_capture`,
`erase_meeting` or `erase_account`; another owner or operation has an independent namespace.

The canonical request is UTF-8 JSON after removing top-level `request_id` and `idempotency_key`,
recursively sorting object keys, preserving array order and emitting no insignificant whitespace.
Replaying the same namespace with the same canonical SHA-256 returns the original operation/result
without a side effect; the response echoes the current attempt's `request_id`. A different canonical
hash in the same namespace returns `409 idempotency_conflict`. `IdempotencyReplayVector` supplies real
mutation request bodies; the validator infers the operation from the closed request shape and computes
the canonical SHA-256 itself. GET status is bounded, credential-free JSON.

| Route | Request `$def` | Success `$def` | Failure `$def` |
|---|---|---|---|
| `ensure` | `EnsureRequest` | `EnsureResponse` | `ErrorResponse` |
| `captures` | `CaptureRequest` | `CaptureResponse` | `ErrorResponse` |
| capture status | — | `StatusResponse` | `ErrorResponse` |
| capture stop | `StopCaptureRequest` | `StatusResponse` | `ErrorResponse` |
| meeting erase | `EraseMeetingRequest` | `ErasureResponse` | `ErrorResponse` |
| account erase | `EraseAccountRequest` | `ErasureResponse` | `ErrorResponse` |
| callback | `CallbackEnvelope` + `SignatureHeaders` | `CallbackAck` | `CallbackErrorResponse` |

Control-route failures use `ErrorResponse`. Its closed code vocabulary separates authentication,
binding, disabled, quota, idempotency conflict, illegal state, invalid input, retryable upstream and
internal failures. Callback failures use `CallbackErrorResponse`, whose closed vocabulary is limited
to authentication, invalid input/state, retryable upstream and internal failures. Neither error shape
carries free-form detail.

## Capture and consent

`CaptureRequest` requires one visible-bot attestation: `bot_visible=true`, a bounded display name,
the exact notice policy version, the attestation timestamp and the same numeric owner as the bound
subject. The server independently enforces policy and lifecycle; the attestation is evidence, not an
authorization bypass. `meeting_url` must be HTTPS, contain no URL credentials and match the declared
platform's recognized provider host/path. Runtime adapters may supply operator-declared Jitsi hosts
through validated configuration; `CaptureRequestValidationVector` carries that host list explicitly,
so ambient process environment never changes sealed conformance. A successful capture creation returns
only `state=requested`. Later states are observed through status and callbacks, limited to `requested
→ joining → awaiting_admission → active → stopping → completed|failed`. A failed state always carries
one named `failure_code`; other states must not. `StatusResponse.metering.terminal` is true exactly for
`completed|failed` and false for all non-terminal lifecycle states.

Retention is explicit and policy-owned. This schema admits operator-selected bounded windows but
does not choose defaults. Summary retention cannot outlive transcript retention. A read never extends
any window; `zaki-read.v1` remains the separate read boundary.

## Metering and idempotency

The Hub reserves wallet units before capture and sends the stable `reservation_id` in
`CaptureRequest`. Usage callbacks identify the event, operation, capture, meeting and reservation;
they report a monotonic sequence plus cumulative `captured_seconds_total`. Consumers settle deltas
idempotently and finalize/refund exactly once when `terminal=true`:

1. A repeated `event_id` returns the duplicate acknowledgement and has no second effect.
2. A sequence lower than the last applied sequence is stale and has no financial effect.
3. The same sequence and cumulative/terminal values have no second financial effect; conflicting
   values for an applied sequence fail closed as `409 invalid_state`.
4. A higher sequence applies only when its cumulative total is not lower than the last applied total;
   settlement is the non-negative delta between those totals.
5. The first applied `terminal=true` finalizes/refunds once. Later events are recorded and
   acknowledged but have no state or financial effect.

All events in one settlement stream must share subject, operation, capture, meeting and reservation
identity. `UsageSettlementVector` executes reordered delivery, duplicate, conflict, decreasing-total
and post-terminal cases. The engine never decides money, plan prices or allowances, and the contract
never exposes wallet balances to the engine.

## Callback authentication

Callbacks narrow the existing `webhook.v1` scheme to mandatory HMAC only:

```text
X-Webhook-Signature = sha256=<hex(HMAC-SHA256(key, X-Webhook-Timestamp + "." + raw_body))>
```

The receiver verifies the signature against the exact bytes received **before JSON parsing**, uses a
constant-time comparison, rejects timestamps more than 300 seconds from receipt, and records
`event_id` before applying state or wallet effects. Authentication failures return
`CallbackErrorResponse` without `event_id`; the receiver may include `event_id` only after both the
signature and parsed envelope are trusted. A repeated event returns `CallbackAck.status = duplicate`
with a 2xx response and performs no second transition or settlement. The legacy
`Authorization: Bearer <secret>` field allowed by generic `webhook.v1` is forbidden in this profile.
`CallbackVerificationVector` covers the exact signed bytes, a syntactically valid wrong signature,
raw-body mutation and stale delivery. It is conformance harness context, not an additional wire
shape.

## Erasure boundary

Meeting and account erasure are owner-scoped, idempotent mutations. `ErasureResponse` contains only
a receipt ID, timestamp and non-negative counts for meeting rows, transcript rows, summary rows and
recording objects. It cannot contain transcript text, attendee data, native storage keys or service
credentials. This receipt proves the Minutes raw-store leg only: Hub account-erasure success still
requires a separate governed Brain receipt and fails loudly if either receipt is missing.

## Privacy and activation invariants

- Operator, tenant and user gates all remain required; no shape implies that a gate is enabled.
- Unknown, expired, erased and foreign resources share the same non-enumerating failure surface.
- Logs, errors, callbacks and receipts contain no transcript/summary body, meeting passcode, storage
  key, service token or HMAC key.
- Callbacks are lifecycle/usage notifications, not a transcript transport.
- This contract adds no runtime route, Secret, migration, deployment, tenant flag or product-state
  change. Minutes remains `coming_soon`, operator-disabled and undeployed until later launch gates.

Run `node validate.mjs` for the focused contract lane and `node scripts/gates.mjs schema` for all
published contract goldens.
