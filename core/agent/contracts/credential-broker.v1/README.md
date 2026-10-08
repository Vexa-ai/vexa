# credential-broker.v1 — the Connections broker wire

The contract between the credential broker (`core/agent/services/credential-broker`) and its three
callers: agent-api's Connections routes (role `agent`), agent-api's Git credential store (role
`git`), and the terminal's server-side Connections routes (role `human`).

> **SEALED** — pinned in `contracts.seal.json`; changes ride the human `lane:contract` review
> (`pnpm seal:contracts` re-pins the hash).

## Surface

| File | What it is |
|---|---|
| `credential-broker.schema.json` | `$defs` for the signed assertion and every request and response body, plus `x-routes`: each route's method, path, the roles allowed to call it, and its body shapes |
| `assertion.py` | the canonical Python signer and verifier, stdlib only. Vendored verbatim into agent-api (`control_plane/broker_assertion.py`) and the broker (`credential_broker/assertion.py`); `gate:fact-parity` fails on a differing byte |
| `golden/` | one vector per shape and case; `SignedAssertionVector.*` are deterministic signing vectors both language bindings must reproduce |
| `validate.mjs` | `gate:schema`: goldens ≡ schema, routes resolve, and the signing vectors re-derive with `node:crypto` |

The TypeScript binding is `clients/terminal/src/app/api/connections/assertion.ts`. It is tested
against the same `SignedAssertionVector` goldens, so the two languages are held to one wire by
three derivations: Python, TypeScript and this validator.

## The assertion

Every request except `GET /health` carries one header:

```
X-Vexa-Assertion: <base64url(claims JSON), unpadded>.<hex HMAC-SHA256(role key, encoded part)>
```

The claims bind role, actor, session, time, a single-use nonce, the method, the exact path with
its query, and the SHA-256 of the exact body bytes. The broker accepts an assertion from 30
seconds before to 5 seconds after its own clock, once. Each role has its own key, so a process
holding the agent key cannot perform a human-only operation.

| Role | Held by | May |
|---|---|---|
| `agent` | agent-api | list, request, prepare, read mail and calendar, create Gmail drafts, call a saved custom service |
| `human` | the terminal's server routes, for a signed-in person | everything `agent` may, plus store a credential, save an OAuth application, start and complete consent, disconnect, delete |
| `git` | agent-api's Git credential store | read and write Git credentials named for the asserted actor, nothing else |

## Deliberately not in this contract

- **No route returns a stored credential to `agent` or `human`.** Only `git` reads a value back,
  and only for Git operations agent-api runs itself.
- **How the broker stores credentials** (an encrypted local store, or OpenBao) is a deployment
  choice behind a port, not part of the wire. See ADR-0040.
- **Who the actor is.** The assertion carries the subject its signer resolved. agent-api resolves
  it from the gateway's identity; the terminal resolves it from the sign-in cookie, checked against
  identity on every request.
