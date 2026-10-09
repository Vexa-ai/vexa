# ADR 0040 — Connections: the credential broker is a product service with its own encrypted store

**Status:** accepted · 2026-10-08 · for v0.13.2 ([#1784](https://github.com/Vexa-ai/vexa/pull/1784),
[#1783](https://github.com/Vexa-ai/vexa/issues/1783)) · applies P10, P15 and P4 · records a
Category B licence decision under [ADR-0004](0004-open-source-dependency-and-license-policy.md)

## Context

v0.13.2 ships Connections: a person connects Gmail, Google Calendar or a custom API to their agent
in the terminal, and the agent then reads mail, creates drafts or calls the service without ever
holding the credential. The product code for it (agent-api's Connections routes and Git store, the
terminal's panel) called a broker that existed only as a development harness in
`deploy/dogfood/credentials-mvp`: no contract, no product image, no deploy surface, tests that ran in
no CI lane, three hand-written copies of its request signature, and an OpenBao that was
single-node, manually unsealed, without TLS, and whose broker token expired after 24 hours.
Production runs agent-api and the terminal, so Connections has to work on a standard install.

## Decision

1. **A separate service, in the agent domain** — `core/agent/services/credential-broker`, image
   `vexaai/v012-credential-broker`, deployed by compose and the chart whenever agent-api is. The
   force that justifies the distribution (P10) is trust isolation: agent-api runs next to the worker
   containers that execute model-chosen tool calls, and the authority to consent and store a
   credential must live in no process an agent can reach.
2. **Three role keys, one wire.** Each caller signs every request with its own key —
   `agent` (agent-api: request, list and use), `human` (the terminal, for the signed-in person:
   consent, store, disconnect, delete), `git` (agent-api's Git store). The assertion binds actor,
   session, time, a single-use nonce, method, path and body. The wire is the sealed contract
   `core/agent/contracts/credential-broker.v1`; one Python signer (vendored byte-for-byte) and one
   TypeScript signer reproduce its golden vectors. No route returns a credential to `agent` or
   `human`.
3. **The default store is the broker's own: AES-256-GCM** over SQLite on the broker's state volume,
   one random nonce per version, path and version bound as associated data. The 32-byte key is a
   secret mounted from somewhere other than that volume (a Kubernetes Secret; a separate compose
   volume), so a copied volume or a backup is ciphertext.
4. **OpenBao (or Vault) is an optional backend** behind the same port
   (`VEXA_CONNECTIONS_STORE=openbao`), for operators who already run one. The chart and compose do
   not ship it. Its path layout is the harness's, so the development deployment keeps its data.
5. **Network.** Only agent-api and the terminal reach the broker: compose puts it on an internal
   network only those two join, and the chart ships a NetworkPolicy admitting their Pods only.
   Worker containers mount no key and cannot open a socket to it.
6. **Not in Lite.** Lite runs agent workers as child processes of its one container, so nothing
   could keep the human key from a worker. Lite answers "Connections are not configured".
7. **One replica.** State is SQLite on a ReadWriteOnce volume; the chart pins `replicas: 1` with a
   Recreate rollout.

## Why not a production OpenBao in the chart

Making the harness's OpenBao production-grade means running it as a three-node Raft cluster with
persistent volumes, TLS between the broker and the vault and between peers, an auto-unseal source,
a bootstrap job that creates the mount, policy and audit device, and a token lifecycle (Kubernetes
auth, or a renewal loop) to replace the 24-hour token. Every self-hoster would operate all of that
for one service. And on a standard install the auto-unseal key is itself a Kubernetes Secret beside
the cluster — the same custody the local store's key has — so the cluster buys availability and an
audit device, not a stronger key-custody property. An operator who needs those already runs a vault
and can point the broker at it. The local store is the smaller system with the same guarantee at
rest.

## Trade-off

- **The broker is a single point of failure for Connections** (one replica, one volume). Agent-api
  answers 503 with a typed fault when it is down; nothing else in the product depends on it. The
  opt-in Git store is the exception: with it switched on, a broker outage refuses Git operations
  rather than falling back to disk.
- **The terminal reaches the broker directly**, past the gateway (ADR-0037 decision 1). It is on the
  dated door backlog: it closes when the gateway re-stamps a person's session as the human role (the
  signed-identity work) and the terminal reaches Connections through `GATEWAY_URL` only.
- **The git role authorizes whatever owner agent-api names.** Since ADR-0041 the agent role is
  bound to the person by the gateway's signed identity, which the broker verifies and matches to the
  assertion's actor. The git role carries no such signature: the broker checks only that the actor
  is the owner in the credential's name, so holding the git key is holding every person's Git
  credentials. The key exists only with the opt-in Git store and is mounted into agent-api alone;
  the network boundary in decision 5 keeps every other process from presenting either role.
- **Key loss is data loss.** Losing the store key makes every stored credential unreadable; people
  reconnect. Rotation without that loss (re-encrypting under a new key) is not built.
- **Deleted connections keep their encrypted versions** until a retention process removes them;
  disconnect does not revoke the grant at the provider.

## Licences

`cryptography` (Apache-2.0 OR BSD-3-Clause) is the broker's one new Python dependency. OpenBao is
MPL-2.0, Category B; it is recorded in `image-licenses.json` as an operator-run optional backend.
Vexa pins and redistributes no OpenBao bytes, and the broker only speaks its HTTP API, so no Vexa
source is MPL-covered. No npm dependency is added.

## Consequences

- The release map moves to schema 3 (twelve images); the release workflows build, pull and probe
  the broker like every other image.
- `gate:config-contract` adopts the broker; agent-api's broker keys are capabilities plumbed on
  compose and helm; the terminal's Connections keys are declared in its own test.
- `gate:domain-doors` counts the broker doors; `gate:fact-parity` holds the vendored signer;
  `gate:contract-conformance` runs the broker's route table against the contract in both directions.
- The development harness is deleted.
