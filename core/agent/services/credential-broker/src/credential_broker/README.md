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
| `assertion.py` | VENDORED from `core/agent/contracts/credential-broker.v1/assertion.py` — edit the canonical copy, then copy it here byte for byte |
| `identity_token.py` | VENDORED from `core/gateway/contracts/gateway-identity.v1/identity_token.py` — verifies the gateway's signature an agent-role call carries; the broker holds the public key only |
| `store.py` | the store port: `LocalEncryptedStore` (AES-256-GCM, default) and `OpenBaoStore` (KV v2) |
| `settings.py` | environment → `Settings`; refuses an unusable configuration at boot |
| `providers.py` | Google OAuth, Gmail and Calendar adapters — fixed URLs and scopes |
| `faults.py` | `UpstreamFault`: Google or a custom service unreachable, rate-limiting or answering unusably — 503/502 and a typed `broker_fault` line, never a refusal the person is told to fix |
| `secret_service.py`, `service_oauth.py`, `connection_setup.py` | custom services: prepared setups, public-HTTPS-only execution, provider-neutral OAuth |
| `setup_schema.py` | the shape of a setup proposal (every allowed key, `extra='forbid'`) and the refusal that names a rejected field; vendored byte for byte to agent-api's `control_plane/connection_setup_schema.py` (`scripts/parity.json`) |
| `obs.py` | `logevent.v1` lines — names and kinds, never values |
| `config.v1.json`, `config_preflight.py` | the config contract; the preflight is VENDORED from `deploy/contracts/config.v1/preflight.py` |

May depend on: the standard library, fastapi, httpx, pydantic, cryptography. Nothing else in the
repository is imported.
