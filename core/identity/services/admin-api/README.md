# admin-api — users + API tokens (Python)

The identity control plane: the `User` + `APIToken` source-of-truth and the HTTP surface that
mints, resolves, and **validates** scoped tokens. Its one job is to be the **gateway's authz
oracle** — `/internal/validate` turns a raw token into `{user_id, scopes, email, …}` so every other
service stays out of the identity business. Python because it carves the parent admin-api
(`libs/admin-models` + FastAPI) clean onto the v0.12 backing stack.

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| **calls** | terminal / dashboard login | `GET /admin/users/email/{email}` | resolve a returning user by email (find-or-create) |
| **calls** | terminal / dashboard login | `POST /admin/users` · `POST /admin/users/{id}/tokens` | create user · mint a scoped session token |
| **consumes** | terminal sign-in doors | `POST /internal/signin-admission` | `{email}` → `{admitted, why}`: an existing user, the admin, or an address on the sign-in allow-list (`VEXA_SIGNIN_ALLOW` + the `signin.allow` setting); anybody while no admin is claimed (Vexa-ai/vexa#1783) |
| **consumes** | the gateway, flows, the terminal's server | `POST /internal/validate` | `ValidateRequest {token}` → `ValidatedIdentity {user_id, scopes, max_concurrent, email, is_admin, webhook_*?, workspaces?}` (fail-closed; `app/validate.py`). A worker's delegation token (`vxd_…`, verified with `VEXA_MCP_DELEGATION_SECRET`) answers the same shape with scopes `bot`+`tx`, no admin role, the dispatch's ceiling as `delegation`, and `person_is_admin` — whether the person it acts for is the instance admin (a fact about them, never a role the worker holds) |
| **consumes** | the terminal's admin settings editor | `GET/PUT /internal/settings/{key}` | the platform-wide defaults (`models`, `transcription`, `setup`, `diagnostics`, `signin`) → `PlatformSettingResponse {key, value, env?, env_problems?}` (`app/platform_settings.py`) |
| **calls** | bot/worker clients | `X-API-Key` on `/user/*` | user-tier self-serve (webhook config in `user.data`) |
| **produces** | Postgres (backing stack) | SQLAlchemy `users` · `api_tokens` | the identity tables (one `Base`, FK `api_tokens.user_id → users.id`) |

## Contracts

**Owns:** [`core/identity/contracts/identity.v1`](../../contracts/identity.v1) — `ScopedToken`
(`subject`, `scopes[]` ∈ `{bot,tx,browser}`, `expires_at`), `AccessDecision` (default-deny verdict),
`ResourceKind` — and [`core/identity/contracts/signin.v1`](../../contracts/signin.v1), the sign-in
admission and admin-claim wire (`app/signin_wire.py` is generated from it). Both sealed in
[`contracts.seal.json`](../../../../contracts.seal.json).
Token prefix/scope rules live in `src/admin_api/token_scope.py` (`VALID_SCOPES`, `vxa_<scope>_…`).

**Consumes:** none — this is the root of the identity domain; it produces the token others validate.

## Isolated evaluation

`tests/` are the Group-1 backing-stack evals — ephemeral testcontainers Postgres + Redis, no live
stack (`conftest.py` skips if Docker is absent). `test_stack_admin_api.py` drives the full surface;
`test_health.py` is the pure-liveness probe.

```bash
uv run pytest -q     # L3 integration (testcontainers Postgres) · L1 health
```

## Status

- ✅ delivered — `User` + `APIToken` tables (one `Base`, v0.12 carve)
- ✅ delivered — admin tier: `POST /admin/users`, `GET /admin/users/email/{email}`, `POST /admin/users/{id}/tokens`, `DELETE /admin/tokens/{id}`
- ✅ delivered — `/internal/validate` authz oracle → `ValidatedIdentity`, fail-closed, expiry-rejecting, `last_used_at` bump; also resolves a worker's delegation token
- ✅ delivered — sign-in allow-list: `POST /internal/signin-admission` + the `signin` platform setting (`app/signin_allow.py`; exact addresses and `@domain` entries; env `VEXA_SIGNIN_ALLOW` merged in)
- ✅ delivered — scoped/multi-scope/expiring token mint (`vxa_<scope>_…`, `VALID_SCOPES`)
- ✅ delivered — user tier self-serve: `/user/webhook`, `/user/calendar(s)`, `/user/models`, `/user/transcription`
- ⬜ planned — `/internal/validate` also returns the canonical `subject` (`u_<user_id>`)
- ⬜ planned — the find-or-create-user + mint-token flow backs the terminal login (Google + dev type-any-email)
