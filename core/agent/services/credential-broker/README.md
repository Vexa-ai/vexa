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
| consumes | agent-api (`routers/connections.py`) | `credential-broker.v1`, role `agent` | request, prepare, list, read, draft, call — results only |
| consumes | agent-api (`git_secret_store.py`) | `credential-broker.v1`, role `git` | Git tokens and deploy keys named for the actor |
| consumes | terminal (`src/app/api/connections/`) | `credential-broker.v1`, role `human` | consent, credential save, disconnect, delete |
| calls | Google OAuth, Gmail, Calendar APIs | fixed URLs in `providers.py` | tokens, reads, drafts |
| calls | a human-approved HTTPS endpoint | `secret_service.py` (public addresses only, DNS pinned, no redirects) | one custom-service request |
| stores | its state volume, or an operator's OpenBao | `store.py` | encrypted credential versions |

The broker is reachable only from agent-api and the terminal: compose puts it on a network nothing
else joins, and the chart ships a NetworkPolicy. Worker containers never hold a role key.

## Contracts

**Owns:** [`core/agent/contracts/credential-broker.v1`](../../contracts/credential-broker.v1) — the
signed role assertion and every route's body. `src/credential_broker/assertion.py` is the
contract's canonical signer, vendored byte-for-byte (`gate:fact-parity`).
**Config:** `src/credential_broker/config.v1.json` (`gate:config-contract`; compose and helm).

## Isolated evaluation

```bash
uv run pytest -q
```

`tests/` covers the assertion boundary (forged, expired, replayed, mis-bound, wrong role), every
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
