# gateway-identity.v1 — the signed identity behind the gateway

The gateway resolves every bearer through identity's `/internal/validate`, then forwards the
request with the identity it resolved. The services behind it (agent-api, meeting-api) read that
identity from `x-user-*` headers. A header is a claim any process on the network can make, so the
gateway also sends **`X-Vexa-Identity`**: the same identity, signed.

```
X-Vexa-Identity: v1.<base64url(claims)>.<base64url(Ed25519(private key, "v1." + base64url(claims)))>
```

**Only the gateway can sign.** It holds the Ed25519 private key; agent-api, meeting-api and the
credential broker hold the public key and can only verify. A verifier that is compromised, or any
process that reaches those services past the gateway, cannot name a person.

`v1` fixes the scheme: the token names no algorithm and none is negotiated. A verifier accepts
exactly a 64-byte Ed25519 signature, in canonical unpadded base64url, and refuses at load time any
key that is not an Ed25519 public key (a private key included), so an HMAC keyed with the public
key, an unsigned token and any other scheme are refused like a forgery.

| Key | Format | Held by | Config |
|---|---|---|---|
| signing key | PKCS#8 PEM (`openssl genpkey -algorithm ed25519`) | the gateway only | `VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE` |
| public key | SubjectPublicKeyInfo PEM (`openssl pkey -pubout`) | agent-api, meeting-api, the credential broker | `VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE` |

The pair is generated at install on every deploy surface: compose's `identity-keys` one-shot, the
Helm chart's identity-keys Secret and ConfigMap, the Lite entrypoint (persisted in its state
directory). Each service refuses to boot without its key, a verifier refuses a private key, and
every service refuses a published test key (RFC 8032 section 7.1 TEST 1 and TEST 2, the keys the
goldens are made with; `PUBLISHED_TEST_KEYS`).

## The door (`IdentityGuard`)

Per request, exactly one of:

| The request carries | Result |
|---|---|
| `X-Vexa-Identity` | signature checked, then `iat`/`exp` within a 30 s skew and a 300 s maximum lifetime; every `x-user-*` header is dropped and rebuilt from the claims, and the verified token stays on the request. Invalid → **401** |
| an `x-user-*` header, no token | believed only from the **internal tier** (`X-Internal-Secret` = `INTERNAL_API_SECRET`): a trusted service acting for the user it names. Otherwise → **401** |
| neither | passes through untouched (health, callbacks that authenticate themselves) |

## Forwarded to the credential broker

agent-api forwards the verified `X-Vexa-Identity` unchanged on every agent-role call to the
credential broker. The broker verifies it with the same public key and refuses the call when it is
missing, invalid, or names a subject other than the actor in the broker assertion
(credential-broker.v1). The internal tier is not accepted there: holding `INTERNAL_API_SECRET` or
the broker's agent key does not let a process act for a person it has no signature for.

## Claims (`#/$defs/Claims`)

| claim | header it becomes |
|---|---|
| `sub` | `x-user-id` |
| `email` | `x-user-email` |
| `scopes` | `x-user-scopes` (comma-joined) |
| `limits` | `x-user-limits` |
| `workspaces` | `x-user-workspaces` (comma-joined) |
| `webhook_url` · `webhook_secret` · `webhook_events` | `x-user-webhook-*` |
| `delegation.regime` · `.workspaces` · `.target` | `x-user-regime` · `x-user-delegation-workspaces` · `x-user-delegation-target` |
| `typ` · `iat` · `exp` | — |

`delegation` is present when the bearer was a worker's delegation token (`vxd_`, minted by
agent-api per dispatch, verified by identity). A service refuses a verb that needs a person in the
loop when `x-user-regime` is not `human`.

## Files

- `identity_token.py` — key loading, the signer, the verifier, the ASGI guard and the install-time
  generator (`python identity_token.py keygen SIGNING PUBLIC`). Standard library plus
  `cryptography`, and **vendored byte for byte** into `core/gateway/services/gateway/src/gateway/`,
  `core/agent/control_plane/`, `core/meetings/services/meeting-api/src/meeting_api/` and
  `core/agent/services/credential-broker/src/credential_broker/`; `gate:fact-parity` compares the
  copies. Edit this one and copy it out.
- `identity.schema.json` — the claims, the signing-vector shape and the refusal-vector shape.
- `golden/` — two payloads, two signing vectors and the refusal vectors, public keys only;
  `validate.mjs` re-signs and re-verifies them in Node with the RFC 8032 TEST 1 key derived from
  its published seed, so the format is pinned in a second language.
