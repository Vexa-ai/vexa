# zaki-control.v1 goldens

Executable positive and negative vectors for the ZAKI Minutes control-plane boundary. Filenames use
`<Shape>.<case>.json`; cases containing `.invalid-` must be rejected.

- Provisioning: enabled policy plus the summary-outliving-transcript negative.
- Capture: visible-bot owner attestation, subject-mismatch rejection and requested response.
- Lifecycle: failed status requires a named code; stop carries a stable idempotency key.
- Callbacks: typed status and cumulative usage events; free-form failure and negative usage reject.
- Signing: mandatory HMAC headers, exact `timestamp.raw_body` vector and stale replay rejection.
- Erasure: meeting/account requests plus a content-free receipt; transcript leakage rejects.
- Errors: closed quota response; free-form detail rejects.

The deterministic signing vector uses the test-only key `zaki-control-v1-contract-test-key`. It is a
public fixture key, never a deployment secret.
