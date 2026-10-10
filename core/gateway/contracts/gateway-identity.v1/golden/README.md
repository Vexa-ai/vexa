# gateway-identity.v1 goldens

- `claims-human.json` — the payload the gateway signs for an API key: subject, email, scopes,
  limits, a workspace membership and a webhook.
- `claims-writer.json` — a member of two workspaces who may write into only one of them:
  `writable_workspaces` is the subset a service checks before letting the caller put something into a
  workspace (binding a meeting, for one).
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

- `refused-expired.json`, `refused-not-yet-valid.json` — the human vector's own token verified past
  `exp` plus the 30 s skew, and more than 30 s before its `iat`.
- `headers-*.json` — the `x-user-*` headers a service rebuilds from `claims-human.json` and
  `claims-delegated.json`; `validate.mjs` re-derives them from the mapping restated in Node.
- `reentry-*.json` — the MCP re-entry match rule, after the signature checks: the bearer's
  `/internal/validate` answer, the signed claims presented in `X-Vexa-Internal-Mcp-Identity`, and
  whether the gateway admits the call. One admitted (same person, same delegation) and four refused:
  another person, a wider delegation, the person's own identity, and an API-key bearer.

The gateway's `tests/test_identity_vectors.py` drives the `reentry-*` and `headers-*` vectors
through `delegation.McpReentry` and `identity_token.headers_from_claims`.

`validate.mjs` re-signs and re-verifies them in Node and fails any golden that carries a PEM
private key; the gateway's `tests/test_identity_token.py` does the same in Python against its
vendored copy, which `gate:fact-parity` holds byte-identical to every other.
