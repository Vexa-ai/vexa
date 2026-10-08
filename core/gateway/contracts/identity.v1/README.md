# identity.v1 — the signed identity behind the gateway

The gateway resolves every bearer through identity's `/internal/validate`, then forwards the
request with the identity it resolved. The services behind it (agent-api, meeting-api) read that
identity from `x-user-*` headers. A header is a claim any process on the network can make, so the
gateway also sends **`X-Vexa-Identity`**: the same identity, signed.

```
X-Vexa-Identity: v1.<base64url(claims)>.<base64url(HMAC-SHA256(secret, "v1." + base64url(claims)))>
```

`secret` is `VEXA_GATEWAY_IDENTITY_SECRET`, held by the gateway (signs) and by agent-api and
meeting-api (verify). It is generated at install on every deploy surface (compose
`mint-dev-env.sh`, the Helm Secret, the lite entrypoint) and each service refuses to boot without it.

## The door (`IdentityGuard`)

Per request, exactly one of:

| The request carries | Result |
|---|---|
| `X-Vexa-Identity` | signature checked in constant time, then `iat`/`exp` within a 30 s skew and a 300 s maximum lifetime; every `x-user-*` header is dropped and rebuilt from the claims. Invalid → **401** |
| an `x-user-*` header, no token | believed only from the **internal tier** (`X-Internal-Secret` = `INTERNAL_API_SECRET`): a trusted service acting for the user it names. Otherwise → **401** |
| neither | passes through untouched (health, callbacks that authenticate themselves) |

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

- `identity_token.py` — the signer, the verifier and the ASGI guard. Standard library only, and
  **vendored byte for byte** into `core/gateway/services/gateway/src/gateway/`,
  `core/agent/control_plane/` and `core/meetings/services/meeting-api/src/meeting_api/`;
  `gate:fact-parity` compares the copies. Edit this one and copy it out.
- `identity.schema.json` — the claims and the signing-vector shape.
- `golden/` — two payloads and two signing vectors; `validate.mjs` re-signs the vectors in Node, so
  the format is pinned in a second language.
