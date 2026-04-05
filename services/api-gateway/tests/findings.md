# API Gateway Test Findings

## Run 2: 2026-03-16 19:33 UTC
Mode: compose-full (localhost:8056)

### Summary
- PASS: 20
- FAIL: 1
- DEGRADED: 1
- SURPRISING: 1

### Results
| Status | Test | Detail |
|--------|------|--------|
| PASS | Health (GET /) | 200 |
| PASS | OpenAPI docs (GET /docs) | 200 |
| PASS | CORS preflight (allowed origin) | access-control-allow-origin: http://localhost:3000 |
| PASS | CORS preflight (evil origin) | No allow-origin header returned — correctly blocked |
| PASS | No auth rejected | 403 |
| PASS | Bad auth rejected | 403 |
| PASS | GET /bots/status | 200, returns running bots |
| PASS | POST /bots (no body) | 422 — backend reached, validation works |
| PASS | DELETE /bots/{platform}/{id} | 404 "No meeting found" — proxy works |
| PASS | PUT /bots/{platform}/{id}/config | 409 "not active" — proxy works, correct rejection |
| PASS | GET /meetings | 200, returns meeting list |
| PASS | GET /transcripts/{platform}/{id} | 200, returns transcript data |
| PASS | PATCH /meetings/{platform}/{id} | 422 validation — proxy works |
| PASS | POST /transcripts/.../share | 200, returns share_id + URL |
| PASS | GET /public/transcripts/{share_id}.txt | 200, returns transcript text (E2E share link works) |
| PASS | POST /meetings/{id}/transcribe | 400 "No recording available" — proxy works |
| PASS | GET /recordings | 200 |
| PASS | GET /recording-config | 200 |
| PASS | MCP proxy | 200, returns JSON-RPC error (correct — no session) |
| PASS | Admin proxy /admin/users | 200 with X-Admin-API-Key: changeme |
| PASS | WebSocket /ws | 101 Switching Protocols |
| PASS | Container stability | 0 restarts |
| PASS | Docker logs | 0 error lines |
| FAIL | PUT /user/webhook | 500 — admin-api StaleDataError (see root cause below) |
| DEGRADED | CORS config | Defaults to localhost:3000,3001. No production domains set. |

### Root Causes

**PUT /user/webhook → 500**: Gateway proxies correctly to admin-api. admin-api throws `sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table 'users' expected to update 1 row(s); 2 were matched`. This is a data integrity issue in admin-api — duplicate user rows for the same email. **Not a gateway bug.**

### Riskiest thing
1. **Webhook endpoint broken** — any user trying to set a webhook gets 500. Root cause is admin-api duplicate users, not gateway.
2. **CORS defaults** — still localhost only. Production will silently block all browser requests.

### Surprising
- Admin proxy uses `X-Admin-API-Key` header (separate from `X-API-Key`). The README documents admin endpoints under "proxied to admin-api" but doesn't clarify the separate auth header. The OpenAPI docs do document it. README could be clearer.

### What was untested
- WebSocket actual message flow (Pub/Sub round-trip) — requires wscat or similar
- Rate limiting — no rate limiting exists in gateway
- Voice agent endpoints (speak, chat, screen, avatar) — would need an active bot session

---

## Run 1: 2026-03-16 22:11:00
Mode: compose-full

### Summary
- PASS: 16
- FAIL: 1
- DEGRADED: 0
- UNTESTED: 0
- SURPRISING: 0

### Results
| Status | Test | Detail |
|--------|------|--------|
| PASS | Health (GET /) | 200 |
| FAIL | Health | HTTP 200 |
| PASS | OpenAPI docs | 200 |
| PASS | CORS headers | Present |
| PASS | Bad auth rejected | HTTP 403 |
| PASS | No auth rejected | HTTP 403 |
| PASS | GET /bots/status | 200 |
| PASS | POST /bots proxy | HTTP 422 (backend reached) |
| PASS | GET /meetings | 200 |
| PASS | Admin proxy /admin/users | 200 |
| PASS | GET /recordings | 200 |
| PASS | GET /recording-config | 200 |
| PASS | MCP proxy reachable | HTTP 200 |
| PASS | WebSocket /ws | HTTP 101 (endpoint exists) |
| PASS | Public transcript 404 | HTTP 404 |
| PASS | Docker logs | 1 error lines |
| PASS | Container stability | 0 restarts |

### Riskiest thing
CORS defaults to localhost — production will silently block all browser requests if not configured.

### What was untested
- WebSocket actual message flow (would need wscat)
- Share link creation and retrieval E2E
- Rate limiting
