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
| consumes | meeting-api (`meeting_api/bot_accounts/`) | `credential-broker.v1`, role `bots` (no `X-Vexa-Identity`; meeting-api acts for a bot) | bot accounts — saved signed-in browser profiles (ADR-0049): list by owner, get, put, state, delete |
| calls | Google OAuth, Gmail, Calendar APIs | fixed URLs in `providers.py` | tokens, reads, drafts |
| calls | a human-approved HTTPS endpoint | `secret_service.py` (public addresses only, DNS pinned, no redirects) | one custom-service request |
| stores | its state volume, or an operator's OpenBao | `store.py` | encrypted credential versions |
| reads once | agent-api's retired `<workspaces>/.secrets/` (operator-run) | `import_git_legacy.py` | Git credentials older agent-api builds kept, moved into `git/<name>` |

The broker is reachable only from agent-api, the terminal and meeting-api: compose puts it on a network nothing
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
- The bots role belongs to meeting-api and is bound to no person: whoever holds its key
  (`VEXA_CONNECTIONS_BOTS_KEY_FILE`) may read every bot account's profile, and meeting-api decides
  who may use which account. It reaches `/api/internal/bot-account` only. A deployment without the
  key has no bot accounts: the route refuses every caller.

## How it works and why it complies

The end-to-end flows (an agent using a connection, a person consenting, agent-api using a Git
credential), the two stores and how `VEXA_CONNECTIONS_STORE` selects one, the single-writer table,
the architecture rules this service meets and the deny tests that prove each protection are in
[`docs/docs/connections.mdx`](../../../../docs/docs/connections.mdx) under "How it works" and
"Why it complies".

## Storage and backup

Both SQLite files (`metadata.sqlite`, and with the local backend `secrets.sqlite`) run in WAL mode
with a 10-second busy timeout (`sqlite_db.py`). WAL is set when the schema opens and is stored in the
file, so a state volume an older broker wrote in rollback-journal mode converts in place on the first
boot. Every write to the secret store folds and truncates the log, so a destroyed version leaves no
copy in it.

`python -m credential_broker.backup` (`backup.py`) snapshots the state into a separate volume on a
schedule: compose's `credential-broker-backup` service and the chart's `state-backup` container,
both on by default (every 6 h, newest 28 kept). It copies the two databases with SQLite's online
backup API from a read-only connection, checks each copy (`integrity_check`), switches it to a
single-file journal and renames the finished `snapshot-<UTC time>/` into place. It opens nothing
else in the state volume and mounts no key: a snapshot is ciphertext, unreadable without the store
key the operator keeps elsewhere. `state-hmac.key` is not copied; it protects consents in flight
only, and the broker generates a new one when it is missing.

### Backup and restore

Restore a snapshot with the broker stopped and the **same store key** it was written under.

Compose (project `vexa`; volume names are `<project>_connections-state` and `<project>_connections-backup`):

```bash
docker compose stop credential-broker credential-broker-backup
docker run --rm -v vexa_connections-backup:/backup alpine ls /backup          # pick a snapshot
SNAP=snapshot-20261023T060000Z
docker run --rm -v vexa_connections-state:/state -v vexa_connections-backup:/backup:ro alpine sh -c "
  rm -f /state/metadata.sqlite* /state/secrets.sqlite* &&
  cp /backup/$SNAP/*.sqlite /state/ && chown 10001:10001 /state/*.sqlite && chmod 600 /state/*.sqlite"
docker compose up -d credential-broker credential-broker-backup
docker compose ps credential-broker      # healthy once /ready answers 200
```

Helm (release `vexa`, namespace `vexa`):

```bash
kubectl -n vexa scale deploy/vexa-vexa-credential-broker --replicas=0
kubectl -n vexa run broker-restore --rm -it --restart=Never --image=alpine --overrides='{"spec":{
  "securityContext":{"runAsUser":10001,"runAsGroup":10001,"fsGroup":10001},
  "volumes":[{"name":"state","persistentVolumeClaim":{"claimName":"vexa-vexa-credential-broker-state"}},
             {"name":"backup","persistentVolumeClaim":{"claimName":"vexa-vexa-credential-broker-backup"}}],
  "containers":[{"name":"r","image":"alpine","stdin":true,"tty":true,"command":["sh"],
    "volumeMounts":[{"name":"state","mountPath":"/state"},{"name":"backup","mountPath":"/backup"}]}]}}'
# inside: ls /backup; rm -f /state/metadata.sqlite* /state/secrets.sqlite*;
#         cp /backup/<snapshot>/*.sqlite /state/ && chmod 600 /state/*.sqlite; exit
kubectl -n vexa scale deploy/vexa-vexa-credential-broker --replicas=1
```

Remove the `-wal` and `-shm` files with the databases (`*.sqlite*`): a log left beside a restored
file belongs to the database it replaced. A wrong store key shows on `/ready` as 503 and in the log
as a `store` fault of kind `integrity`. Consents in flight at snapshot time start again; connections
made after it are reconnected.

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
line. `test_bot_accounts.py` covers the bots role: each action, owner-filtered listing, the
owner/platform pin on put (409), delete destroying every version, the other roles and an unconfigured
key refused, and store faults as 503.

## Status

- ✅ delivered — product service: image `vexaai/vexa-credential-broker`, compose service, helm
  Deployment + Service + PVC + Secret + NetworkPolicy (0.13.2)
- ✅ delivered — local AES-256-GCM store (default) and OpenBao KV v2 store (optional)
- ✅ delivered — Gmail, Google Calendar and custom-secret connections; Git credential store
- 🔨 in progress — bot accounts (role `bots`, ADR-0049): the broker route; meeting-api's caller lands
  separately
- ✅ delivered — retention: a write keeps the current version and the one before it; delete destroys
  every stored version of a connection's credential and OAuth application, disconnect destroys the
  credential (the application stays, for reconnecting). Backups the deployment took keep what they hold.
- ✅ delivered — WAL mode with a busy timeout on both databases; scheduled snapshots of the state
  into a separate volume (compose and helm, on by default), no key in them
- ✅ delivered — each connection record is sealed to its owner inside the encrypted value, and every
  read checks it; the agent role serves a worker only with a person in the loop (listing excepted)
- ⬜ planned — provider-side revocation on disconnect
- ⬜ planned — more than one replica (state is SQLite on one volume; the chart pins `replicas: 1`)
