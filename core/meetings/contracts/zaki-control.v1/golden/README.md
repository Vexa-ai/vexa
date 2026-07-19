# zaki-control.v1 goldens

Executable positive and negative vectors for the ZAKI Minutes control-plane boundary. Filenames use
`<Shape>.<case>.json`; cases containing `.invalid-` must be rejected.

- Provisioning: enabled policy plus the summary-outliving-transcript negative.
- Binding: token tenant/user scope, path owner, ZAKI identity/request headers and mutation body must
  agree; each mismatch axis rejects independently.
- Capture: supported HTTPS provider links, visible-bot owner attestation, URL/subject rejection and a
  requested-only creation response; custom Jitsi hosts are explicit conformance context, with an
  unconfigured-host and attacker-lookalike negative.
- Lifecycle: failed status requires a named code; valid active/completed statuses pin lifecycle and
  metering terminality; every allowed graph edge passes while skipped joins and post-terminal
  transitions reject.
- Callbacks: typed status and cumulative usage events; callback-native authenticated/unauthenticated
  failures; reordered/exact-duplicate settlement is executable, while mutated event replay,
  pre/post-terminal sequence conflict, decreasing totals, free-form failure, request-shaped callback
  errors, auth failures carrying event identity and negative usage reject. Equivalent identity objects
  remain equal when their JSON keys are reordered.
- Signing: mandatory HMAC headers, exact `timestamp.raw_body`, wrong-signature, body-mutation and
  stale replay controls.
- Erasure: meeting/account requests plus a content-free receipt; transcript leakage rejects.
- Errors: closed quota response; free-form detail rejects.
- Idempotency: real mutation bodies are canonicalized and hashed by the harness; key reordering and a
  new request ID replay inside an owner/operation namespace, semantic changes conflict, and cross-owner
  and cross-operation keys remain isolated. Capture, stop, meeting erasure and account erasure each
  have an executable replay pair. A replay preserves the canonical successful-result fingerprint while
  echoing the current request ID; result drift and stale response correlation reject.

The deterministic signing vector uses the test-only key `zaki-control-v1-contract-test-key`. It is a
public fixture key, never a deployment secret.
