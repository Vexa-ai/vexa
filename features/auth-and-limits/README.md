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

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | API rejects requests without valid token | 20 | ceiling | 0 | PASS | 401 on missing token, 200 on valid | 2026-04-05T19:40Z | 02-api |
| 2 | Token scopes enforced (bot, browser, tx) | 20 | ceiling | 0 | PASS | Scopes enforced | 2026-04-05T19:40Z | 02-api |
| 3 | Concurrent bot limit enforced | 20 | ceiling | 0 | PASS | 6/5 concurrent bots correctly rejected | 2026-04-05T19:40Z | 07-bot-lifecycle |
| 4 | Rate limiting works (429 on excess) | 15 | — | 0 | SKIP | Not tested this run | 2026-04-05T19:40Z | 02-api |
| 5 | Token create/revoke via admin API | 15 | — | 0 | SKIP | Admin API responds but explicit token CRUD not tested | 2026-04-05T19:40Z | 02-api |
| 6 | Dashboard token (VEXA_API_KEY) has correct scopes | 10 | — | 0 | PASS | Dashboard POST browser_session → 201, POST meeting join → 201 (requires bot+browser scopes) | 2026-04-05T19:40Z | 04-dashboard |

Confidence: 70 (ceiling items 1+2+3 pass = 60; item 6 = 10; rate limiting + token CRUD not tested = 70/100)
