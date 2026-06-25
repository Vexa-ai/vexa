# core/identity — authN/authZ + accounts (Python)

## Purpose
The identity lane owns **who you are** and **what you may do** for the whole platform: it
authenticates opaque API tokens to a `User`, decides ownership/scope access (default-deny, P20),
and brokers scoped credentials. It exists TWICE on purpose — the live DB-backed
`services/admin-api` (users · scoped tokens · `/internal/validate`, the one auth oracle) and the
pure, DB-free `src/identity_core` reference library — both honoring the frozen `identity.v1` wire
shape. Python because the gate stack (`gate:python` / `gate:stack`) and the admin-api token model
already live here.

## Seams
| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| produces | `core/gateway` (+ any hop) | HTTP `POST /internal/validate` (`X-Internal-Secret`, fail-closed) | raw token → `{user_id, scopes, max_concurrent, email, webhook}`; gateway injects `X-User-ID` downstream |
| consumes | end users / dashboard | HTTP `X-API-Key` (user tier) / `X-Admin-API-Key` (admin tier) | self-serve token use; user/token CRUD |
| produces | any guarded read path | `identity.v1` `AccessDecision` (`canAccess` port) | allow/deny verdict + stable `reason` for `meeting_transcript \| recording \| ws_subscribe` |
| produces | workers needing credentials | `src/identity_core/secrets.py` `SecretsPort` (P15) | a scoped credential whose raw value never hits repr/logs/audit |
| produces | `core/agent`, `core/runtime` consumers | `identity.v1` `ScopedToken` value object | validated subject + scopes + expiry — the identity that travels in a worker's `env` |

## Contracts
**Owns:** [`core/identity/contracts/identity.v1`](contracts/identity.v1) — `ScopedToken`,
`AccessDecision`, `ResourceKind` (sealed in the registry `contracts.seal.json`; goldens under
`contracts/identity.v1/golden/`).
**Consumes:** none — identity is the root of the trust graph; it reads no other lane's `*.v1`.

## Isolated evaluation
- **`tests/`** — pure `identity_core` evals (L1 contract · L2 unit): access deny-tests, token
  mint/validate, secrets-broker non-leak, `identity.v1` golden conformance. Run from `identity/`:
  ```bash
  uv run pytest -q
  ```
- **`services/admin-api/tests/`** — the Group-1 backing-stack evals (L3 integration, testcontainers
  PG + Redis; skip cleanly if docker is absent):
  ```bash
  cd services/admin-api && uv run pytest -q     # or: pnpm gate:stack
  ```

## Status
- ✅ delivered — `identity.v1` sealed (`ScopedToken` · `AccessDecision` · `ResourceKind`) + goldens
- ✅ delivered — `identity_core`: scoped tokens, `canAccess` default-deny owner-only policy (P20), `SecretsPort` broker (P15)
- ✅ delivered — `admin-api`: 3 auth tiers, `/internal/validate` fail-closed oracle, scoped-token minting
- ✅ delivered — Group-1 stack evals (PG schema convergence, redis usage classes, admin-api flow)
- ⬜ planned — map the authed `user_id` → the agent `subject` (`subject = u_<user_id>`) so one user ⇒ one workspace + agents + meetings + routines
