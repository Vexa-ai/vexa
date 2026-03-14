# Admin API

## Why

Users and API tokens must be managed independently of the bot lifecycle. Without a dedicated user management service, every service would need its own auth logic and direct DB access for user operations. The admin-api centralizes user CRUD, token generation/revocation, and analytics queries behind a single authenticated API, keeping the rest of the system stateless with respect to user identity.

## What

A FastAPI service that manages users, API tokens, and platform analytics. It is the only service that writes to the `users` and `api_tokens` tables.

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
| POST | `/admin/users/{user_id}/tokens` | Generate a new API token for a user |
| DELETE | `/admin/tokens/{token_id}` | Revoke an API token |
| GET | `/admin/stats/meetings-users` | Paginated meetings joined with user info |
| GET | `/admin/analytics/users` | User table (no sensitive fields) |
| GET | `/admin/analytics/meetings` | Meeting table (no sensitive fields) |
| GET | `/admin/analytics/meetings/{id}/telematics` | Session, transcription stats, performance metrics for a meeting |
| GET | `/admin/analytics/users/{id}/details` | Full user analytics: meeting stats, usage patterns |
| PUT | `/user/webhook` | Set webhook URL for the authenticated user |

### Dependencies

- **PostgreSQL** -- `users`, `api_tokens`, `meetings`, `meeting_sessions`, `transcriptions` tables via `shared_models`
- **shared_models** -- ORM models, schemas, database session factory, token scope utilities

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
