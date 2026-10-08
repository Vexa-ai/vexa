# credential_broker

| Module | Concern |
|---|---|
| `main.py` | composition root: preflight → settings → store → app (`uvicorn credential_broker.main:app`) |
| `app.py` | the routes, the assertion middleware, metadata and audit (`metadata.sqlite`) |
| `assertion.py` | VENDORED from `core/agent/contracts/credential-broker.v1/assertion.py` — edit the canonical copy, then copy it here byte for byte |
| `store.py` | the store port: `LocalEncryptedStore` (AES-256-GCM, default) and `OpenBaoStore` (KV v2) |
| `settings.py` | environment → `Settings`; refuses an unusable configuration at boot |
| `providers.py` | Google OAuth, Gmail and Calendar adapters — fixed URLs and scopes |
| `secret_service.py`, `service_oauth.py`, `connection_setup.py` | custom services: prepared setups, public-HTTPS-only execution, provider-neutral OAuth |
| `obs.py` | `logevent.v1` lines — names and kinds, never values |
| `config.v1.json`, `config_preflight.py` | the config contract; the preflight is VENDORED from `deploy/contracts/config.v1/preflight.py` |

May depend on: the standard library, fastapi, httpx, pydantic, cryptography. Nothing else in the
repository is imported.
