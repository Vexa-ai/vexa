# Credential broker MVP — isolated synthetic harness

This experiment is not registered in the product or production topology. Use test
credentials in a development prototype only. It demonstrates a trusted human setup panel, OpenBao KV v2,
an agent-facing connection reference, and broker execution with user/session audit.

## Modules and boundaries

- `index.html`, `app.js`, `style.css`: trusted browser panel; secret submission goes
  directly to the broker, never through chat. No localStorage credential storage.
- `app.py`: authenticated connection metadata, owner authorization, human-only
  secret writes, fixed-action broker, idempotency and audit correlation.
- `bao.hcl`: non-dev, single-node Raft OpenBao; unpublished internal listener.
- `fixture.py`: synthetic provider. Only `fixture.verify` is executable; callers
  cannot supply arbitrary destinations, headers, code, or identity claims.
- `bootstrap.py`: operator-only initialization, unseal, audit and policy setup.
- `verify.py`: integration fixtures using actual OpenBao and the fake provider.

Flow: agent requests setup → human panel submits secret → vault stores a version
→ agent requests an allowed operation by connection ID → broker retrieves that
version and injects the credential → agent receives status and receipt.

Actor/session identity comes from authenticated server-side session records.
The broker can access plaintext during execution; the language model cannot
retrieve it through these endpoints. Host operators remain privileged.

## Running this harness

The supplied Compose file targets the development test host (UID/GID 1001) and
its immutable Python runtime image. That image must already exist; this is not
a portable public release. OpenBao is pinned to an official image digest.

From a private harness directory, create state directories owned by UID 1001,
mode 0700: `state/bao-v2`, `state/audit`, `state/broker`, `state/fixture`,
`state/operator`. Then:

```sh
docker compose -p vexa-credentials-mvp -f compose.json up -d bao
# Run bootstrap.py in the Python image on vexa-credentials-mvp_vault,
# with this directory mounted at /mvp, working directory /mvp, UID 1001.
# Do not print recovery.json, broker.token, fixture/key or browser-login.
docker compose -p vexa-credentials-mvp -f compose.json up -d broker fixture
```

Operator verification runs `verify.py` in the Python image on the project's web
network with app.py and verify.py mounted at /mvp, broker state at /state,
fixture state at /fixture (read-only), and operator state at /operator.
It prints only check labels and counts, and saves a one-time synthetic browser
login in the private operator directory. The browser consumes it from a login
fragment, clears the fragment and exchanges it for an HTTPOnly session cookie.
Access the loopback-only port through SSH: localhost:18541 → remote 127.0.0.1:18541.

## Verified 2026-10-07

22 integration checks passed: authentication, one-use bootstrap, agent setup,
human-only storage, no read endpoint, cross-user isolation, sanitized validation,
fixed destination, forged identity rejection, actual provider authentication,
idempotency, cross-session reuse, rotation and refusal of an invalid key, identity
and vault correlation, and absence of the fixture secret in API responses and
SQLite metadata. All six recorded vault request IDs matched vault audit entries.
The fixture secret was absent from vault audit and all three containers' stdout
and stderr. Browser verification also succeeded with a distinct browser session
and visible provider receipt; requesting a connection opened the right panel.
These are bounded checks, not a comprehensive security audit.

## Deliberate limits and next integration

- Synthetic identities; product authentication and MCP tools are not wired in.
- No real email/calendar OAuth, refresh, consent, revocation or provider scopes.
- Single-node/manual one-share unseal, recovery material on the same test host;
  no production availability or key-custody claims. No TLS on internal harness
  networks. Browser access uses an SSH tunnel and localhost HTTP.
- Broker token and demo sessions expire after 24 hours; renewal is not implemented.
- Local SQLite/file audit is not an independent immutable audit sink. Unauthorized
  requests are refused but are not all recorded in the broker action audit.
- Owner isolation is broker-enforced; the vault policy spans harness connections.
- No migration of existing user credentials. Never paste real credentials here.

Before product rollout: integrate product identity and scoped MCP tools; keep
human setup outside model context; implement approved provider adapters and OAuth;
add revoke/refresh lifecycle, independent durable audit, production key custody,
TLS and negative authorization tests across every adapter.

## License

OpenBao uses MPL-2.0. FINOS permits it as a Category B dependency. Directly copied
source requires a CONTRIBUTING notice under FINOS policy; binary redistribution
requires the corresponding covered source to be available and notices retained.
Separate broker code is not automatically MPL-covered. This is license eligibility,
not FINOS security certification; transitive image licenses have not been audited.

- https://community.finos.org/docs/governance/software-projects/license-categories/
- https://www.mozilla.org/en-US/MPL/2.0/FAQ/
- https://openbao.org/docs/audit/

## Provider setup increment — 2026-10-07

The isolated panel includes GitHub tokens, private ICS feeds, Google Calendar and
Gmail. Founder selected Google first; Microsoft adapters are fixtures only and
are hidden in the initial UI. `providers.py` owns the catalog, fixed destinations,
OAuth scopes, token exchange/refresh, input validation and bounded read-only checks.
OAuth starts from the human panel with PKCE and a single-use state bound to the
user/session, expiring after ten minutes. Access and refresh tokens are written to
OpenBao, never API responses or the metadata DB. Disconnect disables broker use;
it does not revoke provider-side grants or destroy historical vault versions.

13 offline provider tests passed, covering missing application config, agent
refusal, session-bound callback/replay, denied consent, scopes, refresh rotation,
secret-safe errors, feed destination/redirect refusal and disabled connections.
The original 22 actual-vault checks passed after deployment of the increment.
Browser confirmed the Google setup panel explicitly refuses to start without
operator application configuration. No real Google grant or provider account was
used. OAuth clients are optional operator configuration under the private state
path; no Google/Microsoft clients were configured in the inspected Minutes terminal.

This increment does NOT migrate existing app.dev credentials or replace its setup
screens. Product identity/MCP wiring, existing GitHub and calendar consumer adapters,
and the actual migration remain open. Existing user connections were not changed.
The demo remains synthetic-only under the custody limitations above.

OAuth is optional for the overall architecture: GitHub token/SSH and read-only ICS
feeds do not require it. Gmail OAuth is the recommended connector; IMAP app passwords
can be an optional fallback for eligible accounts. Do not make OAuth registration a
prerequisite for GitHub or ICS migration. No app-password connector is implemented.

### Operator application setup

With `MVP_OAUTH_CONFIG=vault` (the isolated Compose setting), provider application
client IDs/secrets also live in OpenBao, not the optional fixture JSON file. An
operator-only browser form submits to `/api/operator/google`. Ordinary human and
agent sessions cannot configure it. The operator bootstrap is minted by the
harness operator, never a public endpoint. Do not treat that synthetic role as a
replacement for production RBAC. Shared transport lives in `vault_client.py`.
14 offline provider/security tests passed after this addition.

### Existing production OAuth client reused (supersedes new-client plan)

Founder explicitly requested the existing production `vexa` client. The unsaved
new-client form was cancelled. Its app.dev callback and localhost:3001 callback
are already registered. The harness keeps its trusted panel on localhost:18541;
`MVP_OAUTH_REDIRECT=http://localhost:3001/api/auth/callback/google` uses the existing
registration via a second loopback SSH forward to the same broker. The callback
host exception applies only to GET on the configured callback path, and the
single-use state remains bound to the browser session. All other paths retain
the panel Host check. The callback redirects back to the trusted panel origin.

The existing production application credential was imported directly from the
encrypted secret store into the harness OpenBao operator namespace, without
printing values or writing a plaintext staging file. No Google client, scope
configuration, redirect URI or production deployment was changed. Fifteen provider
fixtures passed. Google recognized the reused client and displayed calendar-event
read consent for the selected account. Consent has NOT been clicked; token exchange
and live account verification remain untested. The consent display alone cannot
prove whether the account previously granted this scope (prompt=consent is used).

The project Data access UI listed no configured scopes at inspection. This is
not evidence about every user's historical token grants. No Gmail consent was
requested. The prototype remains separate from app.dev's identity/consumer paths;
existing calendar/GitHub credentials have not been migrated.


## Minutes and domain-owned MCP integration

`connection_request(provider=google_email|google_calendar)` and `connections_status`
are owned by `core/agent/mcp.tools.v1.json` and agent-api's `routers/connections.py`.
They are absent from a meetings-only MCP deployment. The gateway MCP assembler
combines manifests; agent calls traverse gateway `/agent/*` for identity validation.
No connection tools are registered in the combined legacy dogfood rig.

Minutes `ConnectionsPanel.tsx` polls pending metadata and opens the right-side
panel. Only a human click starts OAuth through the server-only terminal adapter.
The terminal validates the auth cookie against identity; user-info cookies, tool
arguments and browser bodies cannot select a subject. `vxc_` states route the
existing `/api/auth/callback/google` callback to connection setup, leaving login
callbacks untouched. The browser session is bound by a keyed hash of its auth cookie.

Configure `VEXA_CONNECTIONS_BROKER_URL` on terminal and agent-api. Mount DIFFERENT
random keys (at least 32 bytes), with `VEXA_CONNECTIONS_HUMAN_KEY_FILE` only on
terminal and `VEXA_CONNECTIONS_AGENT_KEY_FILE` only on agent-api. Mount both on
broker. Never mount them into agent workers. Set terminal
`VEXA_CONNECTIONS_PUBLIC_ORIGIN=https://app.dev.vexa.ai` and broker
`VEXA_CONNECTIONS_PRODUCT_REDIRECT=https://app.dev.vexa.ai/api/auth/callback/google`.
The deployment must isolate the broker on a private network; use TLS across hosts.
Missing configuration fails closed. Assertions are HMAC-SHA256 over base64url JSON
containing role, actor, session, timestamp, nonce, HTTP method, exact path/query and
SHA256 of request bytes. They expire after 30 seconds and nonces are single-use.
Agent requests receive request-level correlation IDs; browser consent has a stable
login-session audit ID. MCP transport-session attribution is not yet implemented.

Agent authority permits request/status, never consent or operator configuration.
The broker checks connection ownership and single-use OAuth state/session binding.
Tokens remain in OpenBao. Connected means stored authorization; this delivery does
not add email reading tools, event ingestion, auto-join, or email sending.
Existing ICS calendars and GitHub setup are not migrated by this integration.

Validation: `test_product_identity.py`, agent `tests/test_connections.py`, terminal
connection route/panel tests, and MCP assembled-surface tests. Product keys are
separate from the existing prototype sessions; no prototype account is migrated.


Product account selection: `connection_request` accepts `label` and `new_account`;
all Gmail/Calendar operations accept `connection_id`. Multiple ready accounts require
an explicit ID from `connections_status`; omission is only valid for a single match.

Custom secrets: request provider `custom_secret`, then enter the value in the trusted
Connections form. Storage accepts arbitrary text. Optional execution supports an exact
public HTTPS endpoint, GET/POST and a Bearer/raw header selected by the human.
`secret_service_call` accepts the connection ID, query parameters and optional JSON body.
It does not return the secret or support shell/SSH execution. POST can change remote
state and must not be retried automatically on an uncertain outcome.

## Git storage bridge

The control plane can opt into this broker with `VEXA_GIT_STORE_BROKER_URL` and
`VEXA_GIT_STORE_KEY_FILE`. The matching broker key is
`VEXA_CONNECTIONS_GIT_KEY_FILE`; it is separate from human and agent keys and
must never be mounted in a worker, terminal, or MCP process. Only this role may
call `/api/internal/git-secret`. The bridge supports the closed PAT/deploy-key
namespace, stores values in OpenBao KV2, and records request/operation receipts.
Git operations still run in the control plane; the broker releases key material
only to that trusted service. Browser and agent responses remain metadata/public
SSH keys. The role is not authorized for other connection routes.

Legacy Git entries migrate on first read and are removed only after identical
remote read-back. Remote null entries are revocation tombstones. When the broker
is configured, network or vault failure raises instead of falling back to disk.
Deployments without the setting retain the existing local store. Shared-workspace
receipts identify the credential scope, not the initiating human/chat; full
end-user/session propagation remains pending.
