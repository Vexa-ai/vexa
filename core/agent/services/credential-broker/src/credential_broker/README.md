# credential_broker

| Module | Concern |
|---|---|
| `main.py` | composition root: preflight → settings → store → app (`uvicorn credential_broker.main:app`) |
| `app.py` | the app factory: the assertion middleware, the error handlers, the health probe; includes the routes |
| `broker.py` | the shared core every route calls: metadata access, the store port, caller checks, the audit trail, typed fault lines, OAuth helpers |
| `models.py` | the request bodies, one strict model per contract request shape |
| `metadata.py` | the state directory: `metadata.sqlite`'s schema and its additive migration, and the broker-private HMAC key |
| `routes_connections.py` | the agent and human roles' connection routes: lifecycle (setup, prepare, save, consent, disconnect, delete) and use (read, draft, call) |
| `routes_git.py` | the git role's route: agent-api's Git token and deploy-key store |
| `routes_bots.py` | the bots role's route: meeting-api's bot accounts (saved signed-in browser profiles, ADR-0049) — `bot/<id>` sealed with its owner, the `bot-index` metadata, delete destroying every version |
| `routes_sensors.py`, `sensor_grants.py` | the sensors role's calendar-changes read, which reads only under a live grant the person issued on that connection; the person's grant routes (`/api/sensor-grants`: issue, list, revoke; agent and human roles); and the one-time upgrade adoption for calendars the audit shows a sensor was already reading |
| `assertion.py` | VENDORED from `core/agent/contracts/credential-broker.v1/assertion.py` — edit the canonical copy, then copy it here byte for byte |
| `identity_token.py` | VENDORED from `core/gateway/contracts/gateway-identity.v1/identity_token.py` — verifies the gateway's signature an agent-role call carries; the broker holds the public key only |
| `store.py` | the store port: `LocalEncryptedStore` (AES-256-GCM, default) and `OpenBaoStore` (KV v2); `healthy()` is an authenticated check (`/ready`) |
| `settings.py` | environment → `Settings`; refuses an unusable configuration at boot |
| `providers.py` | Google OAuth, Gmail and Calendar adapters — fixed URLs and scopes; re-exports the two below, so callers name `providers.<name>` |
| `provider_base.py` | what every adapter shares: `ProviderError`, the upstream-fault mapping, the bearer header, the single-address check, the Google scopes |
| `calendar_changes.py` | the Google Calendar create, update and cancel adapter (idempotent by request key, only with the calendar-change grant) |
| `action_policy.py` | the connection policy agent-api forwards, enforced again: domains, calendars, notification, attachments; an unattended read only when it allows it (a write is never unattended) |
| `faults.py` | `UpstreamFault`: Google or a custom service unreachable, rate-limiting or answering unusably — 503/502 and a typed `broker_fault` line, never a refusal the person is told to fix |
| `reasons.py` | the typed `reason` every refusal and fault carries (`store_unavailable`, `reconnect_required`, `provider_error`, `sensor_grant_required`; credential-broker.v1 `Error`), and `ReasonedError`, which `app.py` renders beside the sentence |
| `secret_service.py`, `service_oauth.py`, `connection_setup.py` | custom services: prepared setups, public-HTTPS-only execution, provider-neutral OAuth |
| `setup_schema.py` | the shape of a setup proposal (every allowed key, `extra='forbid'`) and the refusal that names a rejected field; vendored byte for byte to agent-api's `control_plane/connection_setup_schema.py` (`scripts/parity.json`) |
| `obs.py` | `logevent.v1` lines — names and kinds, never values |
| `ssrf.py` | VENDORED outbound-URL guard (`scripts/parity.json` fact `outbound-url-guard`) — which URLs a custom-service call may reach |
| `import_git_legacy.py` | the operator-run one-shot move of Git credentials an older agent-api kept on its own volume |
| `config.v1.json`, `config_preflight.py` | the config contract; the preflight is VENDORED from `deploy/contracts/config.v1/preflight.py` |

May depend on: the standard library, fastapi, httpx, pydantic, cryptography. Nothing else in the
repository is imported.
