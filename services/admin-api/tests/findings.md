# Admin API Test Findings

## Run 2: 2026-03-16
Mode: compose-full (admin-api on port 8057)

### Summary
- PASS: 27
- FAIL: 1
- DEGRADED: 0
- SURPRISING: 2

### Results
| Status | Test | Detail |
|--------|------|--------|
| PASS | Health (GET /) | HTTP 200 |
| PASS | OpenAPI docs | HTTP 200 |
| PASS | Postgres connectivity | SELECT 1 succeeds |
| PASS | Required tables exist | users, api_tokens, meetings, meeting_sessions, transcriptions |
| PASS | Auth: no header | HTTP 403 |
| PASS | Auth: wrong token | HTTP 403 |
| PASS | Auth: correct token | HTTP 200 |
| PASS | List users (default) | 200, count=100 |
| PASS | List users (limit=5) | count=5 |
| PASS | List users (limit=5, offset=5) | count=5, different set |
| PASS | Create user | HTTP 201, id=1591 |
| PASS | Idempotent create | HTTP 200, same ID on second call |
| PASS | Missing email rejected | HTTP 422 |
| PASS | Get user by ID | HTTP 200 |
| PASS | Get user by email (not found) | HTTP 404 |
| PASS | Update user (PATCH) | HTTP 200, name updated |
| PASS | Update non-existent user | HTTP 404 |
| PASS | Token create (scope=bot via query) | HTTP 201, prefix=vxa_bot_ |
| PASS | Token create (scope=tx via query) | HTTP 201, prefix=vxa_tx_ |
| PASS | Token create (scope=user via query) | HTTP 201, prefix=vxa_user_ |
| FAIL | Token create (scope=invalid) | HTTP 500 — unhandled ValueError, should be 422 |
| PASS | Token revoke | HTTP 204 |
| PASS | Token revoke (already revoked) | HTTP 404 |
| PASS | Token revoke (non-existent) | HTTP 404 |
| PASS | Scope enforcement: user token on /user/webhook | HTTP 200 (allowed) |
| PASS | Scope enforcement: bot token on /user/webhook | HTTP 403 (denied) |
| PASS | Scope enforcement: tx token on /user/webhook | HTTP 403 (denied) |
| PASS | SSRF: localhost blocked | HTTP 400 |
| PASS | SSRF: 127.0.0.1 blocked | HTTP 400 |
| PASS | SSRF: 10.0.0.1 blocked | HTTP 400 |
| PASS | SSRF: 192.168.1.1 blocked | HTTP 400 |
| PASS | SSRF: valid HTTPS allowed | HTTP 200 |
| PASS | Analytics users | HTTP 200, list, count=1000 |
| PASS | Analytics meetings | HTTP 200, 264K response |
| PASS | Analytics meetings (paginated) | count=3 with limit=3 |
| PASS | Stats meetings-users | HTTP 200 |
| PASS | User details (analytics) | HTTP 200 |
| PASS | Meeting telematics | HTTP 200 |
| PASS | Non-existent user by ID | HTTP 404 |
| PASS | Token for non-existent user | HTTP 404 |
| PASS | Telematics for non-existent meeting | HTTP 404 |
| PASS | User details for non-existent user | HTTP 404 |
| PASS | Analytics without auth | HTTP 403 |
| SURPRISING | Scope param is query, not body | `POST /admin/users/{id}/tokens?scope=bot` — scope in JSON body is silently ignored |
| SURPRISING | No scope column in api_tokens table | Scope only lives in token prefix string. Not queryable. Can't revoke "all bot tokens" without LIKE query |

### Performance
| Endpoint | Response time |
|----------|--------------|
| Health | 1ms |
| List users | 22ms |
| Analytics users | 29ms |
| Analytics meetings (no limit) | 76ms |
| User details | 7ms |
| Meeting telematics | 3ms |

### Riskiest thing
**Invalid scope returns 500** — `POST /admin/users/{id}/tokens?scope=invalid` raises unhandled `ValueError` from `generate_prefixed_token()`. Fix: validate scope in the endpoint before calling the generator, return 422.

### Root cause
`app/main.py:411` — `scope` parameter has no validation. `generate_secure_token()` delegates to `generate_prefixed_token()` which raises `ValueError` for invalid scopes. No try/except or enum constraint on the FastAPI parameter.

### Previous surprising findings resolved
- **Token prefix always `vxa_user_`** (Run 1): This was because scope was passed in the JSON body. Scope is a **query parameter**. When passed correctly (`?scope=bot`), prefixes are correct (`vxa_bot_`, `vxa_tx_`, etc.).

### What was untested
- Stripe integration (no Stripe in compose stack)
- Concurrent request handling / race conditions
- ANALYTICS_API_TOKEN auth (not set in compose env)
- `max_concurrent_bots` field enforcement (stored but unclear if enforced elsewhere)
- Webhook secret generation/handling

---

## Run 1: 2026-03-16 22:08:55
Mode: compose-full

### Summary
- PASS: 17
- FAIL: 1
- DEGRADED: 0
- UNTESTED: 0
- SURPRISING: 2

### Results
| Status | Test | Detail |
|--------|------|--------|
| PASS | Health (GET /) | 200 |
| FAIL | Health (GET /) | HTTP 200 |
| PASS | OpenAPI docs | 200 |
| PASS | Auth: no header | HTTP 403 |
| PASS | Auth: wrong token | HTTP 403 |
| PASS | List users | 200, count=100 |
| PASS | Create user | HTTP 201, id=1589 |
| PASS | Idempotent create | Same ID |
| PASS | Get user by ID | 200 |
| PASS | Get user by email | 200 |
| PASS | Update user (PATCH) | 200 |
| SURPRISING | Bot token prefix | vxa_user_7sW |
| SURPRISING | Transcript token prefix | vxa_user_YtK |
| PASS | Revoke token | HTTP 204 |
| PASS | Analytics users | 200 |
| PASS | Analytics meetings | 200 |
| PASS | Missing email rejected | HTTP 422 |
| PASS | Stats meetings-users | 200 |
| PASS | User webhook | 200 |
| PASS | Docker logs | 0 error lines |
