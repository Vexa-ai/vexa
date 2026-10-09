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

An **agent-role** request also carries the gateway's signed identity for the person it acts for,
forwarded by agent-api exactly as the gateway sent it:

```
X-Vexa-Identity: <gateway-identity.v1 token>
```

The broker verifies it with the gateway's Ed25519 **public** key (`VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE`)
and requires its subject (`sub`) to equal the assertion's `actor`. Missing, invalid, expired, or
signed for anybody else → **401**, the same refusal as a bad assertion. Only the gateway holds the
private key, so holding the agent key is not enough to act for a person: agent-api can use a
person's connections only while it is serving a request the gateway signed for that person. The
`human` role (the terminal resolves the person from the sign-in cookie against identity) and the
`git` role carry no gateway signature.

| Role | Held by | May |
|---|---|---|
| `agent` | agent-api, for the person in the forwarded `X-Vexa-Identity` | list, request, prepare, read mail and calendar, create Gmail drafts, call a saved custom service |
| `human` | the terminal's server routes, for a signed-in person | everything `agent` may, plus store a credential, save an OAuth application, start and complete consent, disconnect, delete |
| `git` | agent-api's Git credential store | read and write Git credentials named for the asserted actor, nothing else |

## Deliberately not in this contract

- **No route returns a stored credential to `agent` or `human`.** Only `git` reads a value back,
  and only for Git operations agent-api runs itself.
- **How the broker stores credentials** (an encrypted local store, or OpenBao) is a deployment
  choice behind a port, not part of the wire. See ADR-0040.
- **How the actor is resolved.** The assertion carries the subject its signer resolved. For the
  `agent` role the broker also checks it against the gateway's signature (above); the terminal
  resolves the `human` actor from the sign-in cookie, checked against identity on every request.
