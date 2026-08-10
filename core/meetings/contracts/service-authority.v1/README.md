# service-authority.v1

The generic, policy-free request/decision contract used by meeting-api before
admitting a bot and at each one-minute active-service boundary.

The request carries only authoritative service facts. The decision carries an
opaque reason and optional stop scope. Payment providers, customer records,
prices, balances, endpoint URLs, credentials, and hosted plan vocabulary are
deliberately absent.

Requests are serialized once and signed over
`<X-Webhook-Timestamp>.<exact-body-bytes>` with HMAC-SHA256. The response echoes
`request_id` and `service_identity`; the consumer rejects cross-bound or stale
responses. Contextual invariants apply across the two schema shapes: an
admission decision cannot carry `stop_scope`, while a denied continuation must
carry `stop_scope: "billable_service"`. A response that violates either rule is
unavailable, never an advisory denial that leaves paid work running.

`service-authority.schema.json` is sealed by `contracts.seal.json`. Goldens are
the wire specification and are validated by `validate.mjs`.
