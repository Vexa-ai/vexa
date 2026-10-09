# gateway-identity.v1 goldens

- `claims-human.json` — the payload the gateway signs for an API key: subject, email, scopes,
  limits, a workspace membership and a webhook.
- `claims-delegated.json` — the payload for a worker's delegation token: the same person, with the
  dispatch's `delegation` ceiling (`autonomous`, one workspace, a target).
- `vector-human.json`, `vector-delegated.json` — signing vectors. Signing `claims` at `now` with
  the RFC 8032 section 7.1 TEST 1 key must produce `token` exactly (Ed25519 is deterministic), and
  `public_key` (its public half) must verify it. No golden carries a signing key: `validate.mjs`
  and the gateway's `tests/rfc8032.py` each derive it from the seed the RFC publishes
  (`RFC8032_TEST1_SEED_HEX`). It is a published test key, so every service refuses it, and TEST 2,
  as the key it is configured with (`identity_token.PUBLISHED_TEST_KEYS`).
- `refused-*.json` — tokens a verifier holding `public_key` must refuse with `reason`: signed by
  another key (RFC 8032 TEST 2), a payload changed after signing, an HMAC-SHA256 keyed with the
  public key (algorithm confusion), no signature, another scheme, and a correctly signed token that
  claims a lifetime over 300 s.

`validate.mjs` re-signs and re-verifies them in Node and fails any golden that carries a PEM
private key; the gateway's `tests/test_identity_token.py` does the same in Python against its
vendored copy, which `gate:fact-parity` holds byte-identical to every other.
