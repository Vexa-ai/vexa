# Admin API

## Why

Users and API tokens must be managed independently of the bot lifecycle. Without a dedicated user management service, every service would need its own auth logic and direct DB access for user operations. The admin-api centralizes user CRUD, token generation/revocation, and analytics queries behind a single authenticated API, keeping the rest of the system stateless with respect to user identity.

## What

A FastAPI service that manages users, API tokens, and platform analytics. It is the only service that writes to the `users` and `api_tokens` tables.

### Documentation
- [Self-Hosted Management](../../docs/self-hosted-management.mdx)
- [Settings API](../../docs/api/settings.mdx)

Three routers provide different access levels:
- **Admin router** (`/admin/*`) -- full CRUD, requires `X-Admin-API-Key` header matching `ADMIN_API_TOKEN`
- **Analytics router** (`/admin/*` read-only subset) -- accepts either admin or analytics token
- **User router** (`/user/*`) -- self-service endpoints authenticated by the user's own `X-API-Key`

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/admin/users` | Find or create a user by email (idempotent) |
| GET | `/admin/users` | List all users (paginated) |
| GET | `/admin/users/{user_id}` | Get user by ID (includes API tokens) |
| GET | `/admin/users/email/{email}` | Get user by email |
| PATCH | `/admin/users/{user_id}` | Update user fields (name, image, max_concurrent_bots, data) |
| POST | `/admin/users/{user_id}/tokens?scopes=bot,tx,browser&name=label` | Generate a new API token for a user (scopes: `bot`, `tx`, `browser` — stored in DB as TEXT[]) |
| DELETE | `/admin/tokens/{token_id}` | Revoke an API token |
| GET | `/admin/stats/meetings-users` | Paginated meetings joined with user info |
| GET | `/admin/analytics/users` | User table (no sensitive fields) |
| GET | `/admin/analytics/meetings` | Meeting table (no sensitive fields) |
| GET | `/admin/analytics/meetings/{id}/telematics` | Session, transcription stats, performance metrics for a meeting |
| GET | `/admin/analytics/users/{id}/details` | Full user analytics: meeting stats, usage patterns |
| PUT | `/user/webhook` | Set webhook URL for the authenticated user |

### Dependencies

- **PostgreSQL** -- `users`, `api_tokens` tables via `admin-models`; `meetings`, `meeting_sessions`, `transcriptions` tables via `meeting-api`
- **admin-models** -- User/APIToken ORM models, token scope utilities, security headers
- **meeting-api** -- Meeting/Transcription models, schemas, webhook delivery
- **schema-sync** -- `ensure_schema()` for startup schema convergence (adds missing columns/tables, no Alembic)

## Data Flow

```
External request (with auth header)
    │
    ▼
FastAPI middleware checks authentication:
    │
    ├── /admin/* endpoints:
    │     requires X-Admin-API-Key header
    │     hmac.compare_digest against ADMIN_API_TOKEN env var
    │
    ├── /admin/analytics/* endpoints:
    │     accepts X-Admin-API-Key (admin) OR ANALYTICS_API_TOKEN (read-only)
    │
    ├── /user/* endpoints:
    │     requires X-API-Key header
    │     validates token against api_tokens table
    │     injects user context
    │
    └── /internal/validate:
          accepts POST with token in body
          returns user_id + scopes (from DB) + email
          updates last_used_at on token
          rejects expired tokens
          requires INTERNAL_API_SECRET (planned — currently optional)
    │
    ▼
Router handles request
    │
    ▼
SQLAlchemy async queries against PostgreSQL
    │
    ├── users table (CRUD, find-or-create by email)
    ├── api_tokens table (scoped: bot/tx/browser — scopes stored as TEXT[] in DB)
    ├── meetings table (analytics joins)
    ├── meeting_sessions table (telematics)
    └── transcriptions table (stats)
    │
    ▼
Response returned (JSON)
```

## Code Ownership

```
services/admin-api/app/main.py        → all routes (admin, analytics, user, internal), auth middleware
services/admin-api/app/scripts/       → database management scripts (recreate_db.py)
services/admin-api/tests/             → 6 test files (auth, CRUD, validate, JSONB merge, gate)
services/admin-api/Dockerfile         → container build (includes admin-models + meeting-api packages)
shared/admin-models/                  → ORM models (User, APIToken), database session, security headers
shared/meeting-api/                   → Meeting/Transcription models, schemas, webhook validation
```

## How

### Run

```bash
# Via docker-compose (from repo root)
docker compose up admin-api

# Standalone (from repo root, with venv active)
cd services/admin-api
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

### Configure

| Variable | Description |
|----------|-------------|
| `ADMIN_API_TOKEN` | Secret token required in `X-Admin-API-Key` header for admin endpoints |
| `ANALYTICS_API_TOKEN` | Optional read-only token accepted by analytics endpoints |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL connection |
| `DB_SSL_MODE` | SSL mode for DB connection (default: `disable`) |
| `LOG_LEVEL` | Logging level (default: `INFO`) |

### Test

```bash
# Health check
curl http://localhost:8001/

# Create a user (requires admin token)
curl -X POST http://localhost:8001/admin/users \
  -H "X-Admin-API-Key: $ADMIN_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com"}'
```

### Debug

- Logs go to stdout with format `%(asctime)s - admin_api - %(levelname)s - %(message)s`
- Set `LOG_LEVEL=DEBUG` for verbose output
- If `ADMIN_API_TOKEN` is not set, all admin endpoints return 500
- OpenAPI docs at `http://localhost:8001/docs`

## Production Readiness

**Confidence: 62/100**

| Area | Score | Evidence | Gap |
|------|-------|----------|-----|
| User CRUD | 8/10 | Idempotent create (200/201), paginated list, email lookup, JSONB merge on PATCH | JSONB merge is shallow (nested objects overwritten, not deep-merged); `created_at` null repair repeated 9 times |
| Token management | 8/10 | Scopes stored in DB as TEXT[], multi-scope support, name/expires_at/last_used_at columns, 422 for invalid scopes | Legacy backfill runs on startup (idempotent). No token rotation mechanism. |
| /internal/validate | 8/10 | Reads scopes from DB, updates last_used_at, rejects expired, enforces INTERNAL_API_SECRET (fail-closed) | 60s Redis cache in gateway means last_used_at slightly delayed. No cache invalidation on revocation. |
| Analytics queries | 7/10 | Meeting stats, user details, telematics with proper joins to meeting_api.models | Queries may be slow on large tables (no pagination on some analytics endpoints) |
| Webhook endpoint | 7/10 | SSRF validation via `meeting_api.webhook_url.validate_webhook_url()` | Webhook secret stored in plaintext JSONB (not hashed) |
| Meeting-api integration | 9/10 | Dockerfile correctly COPYs and pip-installs meeting-api package; imports work | Tight coupling — if meeting-api package is removed/renamed, build breaks with no fallback |
| Auth middleware | 8/10 | Admin endpoints require X-Admin-API-Key; analytics accepts admin OR analytics token; user endpoints validate X-API-Key with scope check | No rate limiting on any endpoint |
| Tests | 7/10 | test_auth.py (admin/analytics token), test_validate.py (token validation), test_crud.py (all CRUD), test_jsonb_merge.py (JSONB merge) | Tests don't verify /internal/validate lacks caller auth (the critical gap). No integration tests against real DB |
| Docker | 8/10 | Includes admin-models + meeting-api; non-root user (appuser) | No HEALTHCHECK |
| Security | 5/10 | Scope validation correct; SSRF protection on webhooks | /internal/validate unauthenticated; no rate limiting; webhook secret in plaintext; no brute-force protection on token validation |

### Known Limitations

1. **`/internal/validate` caller auth enforced** — `INTERNAL_API_SECRET` is mandatory. Missing/wrong secret → 403. No secret configured + DEV_MODE=false → 503. ~~Previously optional.~~ Fixed 2026-03-29.
2. **Legacy tokens backfilled** — tokens without `vxa_` prefix now have `['bot', 'tx']` scopes in DB. Tokens with removed scopes (`user`, `admin`) migrated to `['bot', 'tx']`. Backfill runs on startup, idempotent. Fixed 2026-03-29.
3. **Webhook secret stored in plaintext** — `webhook_secret` is stored as a plain string in the user's JSONB `data` column. Not hashed, not encrypted at rest (beyond DB-level encryption).
4. **`created_at` null repair pattern** — the same 5-line null check for `created_at` is copy-pasted 9 times throughout main.py. Root cause is SQLAlchemy async `refresh()` not loading server-side defaults. Should be a utility function.
5. **No rate limiting** — token validation, user creation, and analytics endpoints have no rate limiting. Token brute-force is theoretically possible (though token space is large).
6. **Shallow JSONB merge** — `PATCH /admin/users/{id}` with `{"data": {"nested": {"b": 2}}}` will overwrite any existing `nested` object entirely, not merge it.

### Validation Plan (to reach 90+)

- [x] **P0**: Enforce INTERNAL_API_SECRET on `/internal/validate` — done 2026-03-29
- [x] **P0**: Add scopes (TEXT[]), name, last_used_at, expires_at columns to api_tokens (via ensure_schema) — done 2026-03-29
- [x] **P0**: /internal/validate reads scopes from DB, updates last_used_at, rejects expired — done 2026-03-29
- [x] **P1**: Backfill existing tokens: parse prefix → scopes array; legacy → ['bot','tx']; removed scopes migrated — done 2026-03-29
- [x] **P1**: Validate scope param on token creation (reject invalid/removed scopes with 422) — done 2026-03-29
- [ ] **P2**: Extract `created_at` null repair into a utility function to reduce duplication
- [ ] **P2**: Hash webhook secrets before storage (or document that plaintext is intentional)
- [ ] **P2**: Add HEALTHCHECK to Dockerfile
- [ ] **P3**: Add integration tests against real PostgreSQL (testcontainers or docker-compose test profile)
- [ ] **P3**: Add pagination to analytics endpoints that currently return unbounded results

## Constraints

- admin-api is the ONLY service that writes to `users` and `api_tokens` tables — no other service has DB write access for user data
- All authentication flows resolve through admin-api — either directly or via `/internal/validate` from api-gateway
- Token scoping: `bot`, `tx`, `browser` — `admin` and `user` scopes removed (dead code). Scopes stored in DB TEXT[] column (source of truth).
- `X-Admin-API-Key` uses constant-time comparison (`hmac.compare_digest`) — timing attacks mitigated
- PostgreSQL is the only data store — no Redis, no caching layer
- Depends on `admin-models` and `meeting-api` shared packages — Dockerfile COPYs and installs both
- `/internal/validate` is hidden from OpenAPI but network-accessible — relies on network-level isolation
- SSRF validation on webhook URLs via `meeting_api.webhook_url.validate_webhook_url()`
- README.md MUST be updated when behavior changes

## Known Issues

- deploy/compose/docker-compose.yml missing INTERNAL_API_SECRET — agentic stack has it, prod compose does not
- ~~`/internal/validate` caller auth optional~~ — fixed 2026-03-29, now enforced
- ~~Legacy tokens bypass scope checks~~ — fixed 2026-03-29, backfilled to ['bot','tx']
- ~~Token scopes not in DB~~ — fixed 2026-03-29, scopes TEXT[] column added
- Webhook secret stored in plaintext JSONB (not hashed)
- `created_at` null repair pattern copy-pasted 9 times (SQLAlchemy async refresh issue)
- No rate limiting on any endpoint — token brute-force theoretically possible
- Shallow JSONB merge on PATCH — nested objects overwritten, not deep-merged
- No HEALTHCHECK in Dockerfile
- No integration tests against real PostgreSQL
- Some analytics endpoints return unbounded results (no pagination)
