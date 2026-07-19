# zaki-control.v1 goldens

Executable positive and negative vectors for the ZAKI Minutes control-plane boundary. Filenames use
`<Shape>.<case>.json`; cases containing `.invalid-` must be rejected.

- Provisioning: enabled policy plus the summary-outliving-transcript negative.
- Capture: supported HTTPS provider links, visible-bot owner attestation, URL/subject rejection and a
  requested-only creation response; custom Jitsi hosts are explicit conformance context, with an
  unconfigured-host negative.
- Lifecycle: failed status requires a named code; lifecycle and metering terminality must agree.
- Callbacks: typed status and cumulative usage events; callback-native authenticated/unauthenticated
  failures; reordered/duplicate settlement is executable, while sequence conflict, decreasing totals,
  free-form failure, request-shaped callback errors and negative usage reject.
- Signing: mandatory HMAC headers, exact `timestamp.raw_body`, wrong-signature, body-mutation and
  stale replay controls.
- Erasure: meeting/account requests plus a content-free receipt; transcript leakage rejects.
- Errors: closed quota response; free-form detail rejects.
- Idempotency: real mutation bodies are canonicalized and hashed by the harness; key reordering and a
  new request ID replay inside an owner/operation namespace, semantic changes conflict, and cross-owner
  and cross-operation keys remain isolated.

The deterministic signing vector uses the test-only key `zaki-control-v1-contract-test-key`. It is a
public fixture key, never a deployment secret.
