# credential-broker — the Connections credential broker (Python)

## Purpose

Holds the third-party credentials a person connects — a Gmail or Google Calendar authorization, an
API key or OAuth application for a custom service, a saved Git token — and uses them on that
person's behalf without returning them. The agent asks for a connection, a human consents or pastes
the secret in the terminal's Connections panel, and from then on the agent receives results (mail,
events, a service response, a Git credential for agent-api's own Git operations), never the
credential. Decision record: [ADR-0040](../../../../docs/adr/0040-connections-credential-broker-is-a-product-service.md).

A separate service rather than an agent-api module (P10) because its force is trust isolation:
agent-api runs next to the worker containers that execute model-chosen tool calls, and the broker's
human key — the authority to consent and store — must live in no process an agent can reach.

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| consumes | agent-api (`routers/connections.py`) | `credential-broker.v1`, role `agent`, plus the gateway's `X-Vexa-Identity` for the same person (`gateway-identity.v1`) | request, prepare, list, read, draft, call — results only |
| consumes | agent-api (`git_secret_store.py`) | `credential-broker.v1`, role `git`, plus the gateway's `X-Vexa-Identity` for the same person | the person's Git token and deploy keys, and the deploy keys of shared workspaces signed as theirs |
| consumes | terminal (`src/app/api/connections/`) | `credential-broker.v1`, role `human` | consent, credential save, disconnect, delete |
| calls | Google OAuth, Gmail, Calendar APIs | fixed URLs in `providers.py` | tokens, reads, drafts |
| calls | a human-approved HTTPS endpoint | `secret_service.py` (public addresses only, DNS pinned, no redirects) | one custom-service request |
| stores | its state volume, or an operator's OpenBao | `store.py` | encrypted credential versions |

The broker is reachable only from agent-api and the terminal: compose puts it on a network nothing
else joins, and the chart ships a NetworkPolicy. Worker containers never hold a role key.

Two probes answer without an assertion: `GET /health` is liveness (the process answers; it stays
ok while the store is down, since a restart does not bring a store back) and `GET /ready` is
readiness (503 `unavailable` while the credential store does not answer, logged as a `store`
fault).

Both roles agent-api holds, `agent` and `git`, are bound to a person by the gateway rather than by
agent-api. Every call carries the gateway's signed identity for the person the request acts for,
forwarded unchanged; the broker verifies it with the gateway's public key
(`VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE`) and refuses the call unless it names the assertion's actor.
The git role then serves only that person's own names (`pat/<person>`, `deploy/user-<person>.*`)
and the deploy keys of shared workspaces among the memberships signed with them (`deploy/ws-<id>.*`).

What this does and does not stop:

- agent-api's keys alone act for nobody. A request needs a gateway signature the broker accepts.
- agent-api does see every signature a person's request carries. A signature lives 60 seconds as
  the gateway signs it; the broker accepts at most 300 seconds, plus 30 seconds of clock skew. A
  compromised agent-api can therefore act, through either role, for anyone whose request it served
  within that window, and for nobody else.
- A shared workspace's members are decided by its own `policy/members.json`, which agent-api keeps.
  The gateway signs admin-api's mirror of that list, and agent-api writes the mirror. For
  `deploy/ws-` keys the broker's check is therefore only as strong as that mirror, and a member the
  mirror has missed is refused.
- The human role belongs to the terminal. The terminal resolves the person from its sign-in cookie,
  and the broker trusts the terminal's key for that.

## Contracts

**Owns:** [`core/agent/contracts/credential-broker.v1`](../../contracts/credential-broker.v1) — the
signed role assertion and every route's body. `src/credential_broker/assertion.py` is the
contract's canonical signer, vendored byte-for-byte (`gate:fact-parity`).
**Consumes:** [`core/gateway/contracts/gateway-identity.v1`](../../../gateway/contracts/gateway-identity.v1)
— the gateway's signed identity; `src/credential_broker/identity_token.py` is its verifier, vendored
byte-for-byte.
**Config:** `src/credential_broker/config.v1.json` (`gate:config-contract`; compose and helm).

## Isolated evaluation

```bash
uv run pytest -q
```

`tests/` covers the assertion boundary (forged, expired, replayed, mis-bound, wrong role), the
forwarded identity on the agent and git roles (missing, forged, expired, another person's, a
shared-workspace key outside the signed memberships), every
route against the contract in both directions (`test_contract_conformance.py`), the two store
adapters, the Google and custom-service adapters offline, M3's host confirmation, and a boot from a
real environment with the encrypted store, asserting a canary secret appears in no file and no log
line.

## Status

- ✅ delivered — product service: image `vexaai/v012-credential-broker`, compose service, helm
  Deployment + Service + PVC + Secret + NetworkPolicy (0.13.2)
- ✅ delivered — local AES-256-GCM store (default) and OpenBao KV v2 store (optional)
- ✅ delivered — Gmail, Google Calendar and custom-secret connections; Git credential store
- ⬜ planned — provider-side revocation on disconnect, and a retention job that destroys stored
  versions of deleted connections (today they are kept, encrypted, under the deployment's backups)
- ⬜ planned — more than one replica (state is SQLite on one volume; the chart pins `replicas: 1`)
