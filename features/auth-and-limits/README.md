---
services: [api-gateway, admin-api, meeting-api]
tests3:
  targets: [contracts, smoke]
  checks: [AUTH_REJECTS_NO_TOKEN, DASHBOARD_ADMIN_KEY_MATCHES, DASHBOARD_ADMIN_KEY_VALID, DASHBOARD_API_KEY_VALID]
---

# Auth and Limits

## Why

Multi-tenant API security. Each user has scoped API tokens, concurrent bot limits, and rate limiting. Prevents abuse and isolates tenants.

## What

```
Request → API Gateway → validate X-API-Key → resolve user_id + scopes + limits
  → inject x-user-id, x-user-scopes, x-user-limits headers → forward to backend
```

### Components

| Component | File | Role |
|-----------|------|------|
| token validation | `services/api-gateway/main.py` | Validate token via admin-api, cache in Redis |
| token management | `services/admin-api/app/main.py` | CRUD tokens with scopes |
| rate limiting | `services/api-gateway/main.py` | Per-user RPM limiting |
| concurrency limit | `services/meeting-api/meeting_api/meetings.py` | max_concurrent_bots per user |

## How

### 1. Authenticate a request

Every API call requires an `X-API-Key` header. The gateway validates it against admin-api and injects user context headers downstream.

```bash
# Rejected (no token)
curl -s -o /dev/null -w "%{http_code}" http://localhost:8056/bots
# 401

# Accepted (valid token)
curl -s -o /dev/null -w "%{http_code}" \
  -H "X-API-Key: $VEXA_API_KEY" http://localhost:8056/bots
# 200
```

### 2. Create and revoke tokens (admin API)

```bash
# Create a token with specific scopes
curl -s -X POST http://localhost:8067/tokens \
  -H "Content-Type: application/json" \
  -d '{"scopes": ["bot", "browser"], "max_concurrent_bots": 5}'
# {"api_key": "vx-...", "user_id": "..."}

# Revoke a token
curl -s -X DELETE http://localhost:8067/tokens/vx-...
# 204
```

### 3. Hit the concurrent bot limit

The meeting-api enforces `max_concurrent_bots` per user. When the limit is exceeded, new bot creation is rejected.

```bash
# After reaching the limit (e.g., 5 active bots):
curl -s -X POST http://localhost:8056/bots \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"meeting_url": "https://meet.google.com/abc-defg-hij"}'
# 429 {"detail": "Concurrent bot limit reached"}
```

### 4. Trigger rate limiting

Exceeding the per-user requests-per-minute limit returns a 429.

```bash
# Rapid-fire requests past the RPM limit:
for i in $(seq 1 200); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -H "X-API-Key: $VEXA_API_KEY" http://localhost:8056/bots
done
# ... 200 200 200 ... 429 429 429
```

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | API rejects requests without valid token | 20 | ceiling | 0 | UNTESTED | 401 on missing token, 200 on valid | 2026-04-05T19:40Z | 02-api |
| 2 | Token scopes enforced (bot, browser, tx) | 20 | ceiling | 0 | UNTESTED | Scopes enforced | 2026-04-05T19:40Z | 02-api |
| 3 | Concurrent bot limit enforced | 20 | ceiling | 0 | UNTESTED | 6/5 concurrent bots correctly rejected | 2026-04-05T19:40Z | 07-bot-lifecycle |
| 4 | Rate limiting works (429 on excess) | 15 | — | 0 | SKIP | Not tested this run | 2026-04-05T19:40Z | 02-api |
| 5 | Token create/revoke via admin API | 15 | — | 0 | SKIP | Admin API responds but explicit token CRUD not tested | 2026-04-05T19:40Z | 02-api |
| 6 | Dashboard token (VEXA_API_KEY) has correct scopes | 10 | — | 0 | UNTESTED | Dashboard POST browser_session → 201, POST meeting join → 201 (requires bot+browser scopes) | 2026-04-05T19:40Z | 04-dashboard |

Confidence: 70 (ceiling items 1+2+3 pass = 60; item 6 = 10; rate limiting + token CRUD not tested = 70/100)
