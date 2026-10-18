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

agent-api forwards the verified `X-Vexa-Identity` unchanged on every agent- or git-role call to the
credential broker. The broker verifies it with the same public key and refuses the call when it is
missing, invalid, or names a subject other than the actor in the broker assertion
(credential-broker.v1). The internal tier is not accepted there: holding `INTERNAL_API_SECRET` or
the broker's agent or git key does not let a process act for a person it has no signature for.

## Re-entry: a worker's tool call back into the gateway (`#/$defs/ReentryHeader`)

A worker's delegation token (`vxd_…`, delegation.v1) is admitted on `/mcp`, on the routes.v1 rows
marked `delegation` (the friction report its harness files), and on the rows marked `mcp_reentry`
only when the MCP is the caller. The MCP's tools act by calling back into the gateway's REST routes
with the same bearer, so the gateway has to tell *the MCP acting on an `/mcp` request it already
admitted* from *a worker calling REST directly*. It does so with an identity only it can sign:

| | |
|---|---|
| **Header** | `X-Vexa-Internal-Mcp-Identity` (case-insensitive; in the `x-vexa-internal-` family, which the gateway never forwards from a client to a service behind it) |
| **Value** | the `X-Vexa-Identity` token the gateway signed onto the `/mcp` request being served |
| **Carrier** | the MCP service (`vexa_mcp/reentry.py`): it holds the inbound `X-Vexa-Identity` for the life of the request and sends it back only on calls to the gateway. It holds no key and verifies nothing |
| **Verifier** | the gateway, with its own key's public half: signature, then `iat`/`exp` (30 s skew, 300 s lifetime) |
| **Match rule** | admitted only when the bearer resolves to a delegation now, the signed `sub` equals the bearer's user, and the signed `delegation` equals the one the gateway would sign for the bearer now (`regime`, `workspaces`, `target`) |

Anything else is not re-entry, and the call answers 403 like any other delegated REST call: no
header, a forged or expired token, another person's identity, a wider delegation, or the person's own
identity (no `delegation`). The `reentry-*` goldens pin the match rule; `validate.mjs` restates it.

Re-entry that matches is still admitted only on a route the MCP's tools call back into — a route
whose `routes.v1` row says `"mcp_reentry": true` (gateway `routes_manifest.py`). This contract fixes
what re-entry IS; which routes accept it is the owning domain's declaration, not part of the header.

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
agent-api per dispatch, verified by identity). Identity answers such a token only to a resolver that
declares it reads `delegation` (identity.v1 `AcceptsDelegationHeader`,
`X-Vexa-Internal-Accepts-Delegation: 1`), and the gateway sends it on every validate hop. **A gateway
older than v0.13.2 does not, so a v0.13.2 identity refuses it every delegation token (401): it fails
closed, never forwarding a worker as its person without `delegation`.** Upgrade the gateway, the MCP,
admin-api, agent-api and flows-api together. A service refuses a verb that needs a person in the
loop when `x-user-regime` is not `human`. `identity_token.py` carries that rule for every service:
`is_delegated(headers)` (any delegation header present, an empty one included), `is_unwatched(headers)`
(delegated, and a regime other than `human`) and `REFUSAL`, the one 403 body a worker reads.

## Files

- `identity_token.py` — key loading, the signer, the verifier, the ASGI guard and the install-time
  generator (`python identity_token.py keygen SIGNING PUBLIC`). Standard library plus
  `cryptography`, and **vendored byte for byte** into `core/gateway/services/gateway/src/gateway/`,
  `core/agent/control_plane/`, `core/meetings/services/meeting-api/src/meeting_api/` and
  `core/agent/services/credential-broker/src/credential_broker/`; `gate:fact-parity` compares the
  copies. Edit this one and copy it out.
- `identity.schema.json` — the claims, the re-entry header and match vector, the signing-vector shape
  and the refusal-vector shape.
- `golden/` — two payloads, two signing vectors, the refusal vectors and the re-entry match vectors,
  public keys only;
  `validate.mjs` re-signs and re-verifies them in Node with the RFC 8032 TEST 1 key derived from
  its published seed, so the format is pinned in a second language.
