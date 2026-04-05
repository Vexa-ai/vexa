---
id: test/api-full
type: validation
requires: [test/infra-up]
produces: [USER_ID, API_TOKEN, WEBHOOK_URL]
validates: [auth-and-limits]
docs: [features/auth-and-limits/README.md, services/admin-api/README.md, services/api-gateway/README.md, services/agent-api/README.md, services/transcription-service/README.md]
mode: machine
skill: /test-api
---

# API Full Test

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Test all API endpoints that don't require real meetings or browsers. Covers admin API, meeting API CRUD, runtime API profiles, agent API health, MCP, and webhooks.

## Inputs

| Name | From | Default | Description |
|------|------|---------|-------------|
| GATEWAY_URL | test/infra-up | — | API gateway base URL |
| ADMIN_URL | test/infra-up | — | Admin API base URL |
| ADMIN_TOKEN | test/infra-up | — | Admin API auth token |

## Script

```bash
eval $(./scripts/02-api.sh GATEWAY_URL ADMIN_URL ADMIN_TOKEN)
```

See [02-api.sh](scripts/02-api.sh) for implementation.

## Steps

1. Load test user from `secrets/staging.env` (TEST_USER_ID, TEST_USER_EMAIL, TEST_API_TOKEN). Create via `scripts/create-test-user.sh` only if not present.
2. Admin API — list users, get user
3. Meeting API — list meetings, list bots
4. Runtime API — profiles (assert `meeting` exists)
5. Agent API — health
6. Transcription service — full pipeline check:
   a. Health: `GET /health` → gpu_available=true
   b. From inside container: `docker exec $CONTAINER curl -sf $TRANSCRIBER_URL/../health` → same result (proves bots can reach it)
   c. WAV transcription from inside container: send `testdata/test-speech-en.wav` via `docker exec`, assert non-empty text returned
   d. Env check: `TRANSCRIBER_URL` and `TRANSCRIBER_API_KEY` are set inside the container
   e. If past meetings exist in DB: `GET /transcripts/{platform}/{native_id}` for at least one meeting → assert segments returned (proves read path works end-to-end)
   > The host-level health check alone is NOT sufficient. Bots run inside the container
   > and must reach the transcription service from there. G8: health=true is not specific
   > to "bots can transcribe" — it only proves the GPU service is up.
7. MCP — endpoint responds
8. Webhook/settings endpoint
9. WebSocket — basic connectivity:
   a. Connect `ws://localhost:$GATEWAY_PORT/ws?api_key=$API_TOKEN` → assert accepted
   b. Send `{"action":"ping"}` → assert `{"type":"pong"}`
   c. Connect without key → assert rejected with close code 4401
   > This is a smoke test. Full WS validation is in 12-websocket.md.
   > But basic connectivity must be verified here because the dashboard
   > depends on WS for live transcripts. If WS is broken, the dashboard
   > shows no live data even when REST works fine.

## Outputs

| Name | Description |
|------|-------------|
| USER_ID | Test user numeric ID |
| API_TOKEN | API token with bot,browser,tx scopes |
| WEBHOOK_URL | Registered webhook URL (if configured) |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| Transcription health PASS but no transcripts from bots | Host can reach transcription service but bots inside container cannot (different network, wrong TRANSCRIBER_URL) | Test from inside container: `docker exec $CONTAINER curl -sf $TRANSCRIBER_URL` | 2026-04-05: host health check passed, no one tested from inside lite container. G8: health signal is not specific to "pipeline works". |
| Dashboard shows no live transcripts | WebSocket not tested — REST works but WS broken | Add WS ping/pong to API test. Dashboard uses WS for live segments. | 2026-04-05: 02-api had no WS check, dashboard showed no live data. |

## Docs ownership

After this test runs, verify and update:

- **features/auth-and-limits/README.md**
  - DoD table: update Status, Evidence, Last checked for items #1 (reject without valid token), #2 (scope enforcement for bot/browser/tx), #4 (rate limiting 429), #5 (token create/revoke)
  - Components table: verify file paths (`services/api-gateway/main.py`, `services/admin-api/app/main.py`, `services/meeting-api/meeting_api/meetings.py`) still contain the referenced auth/rate-limit logic
  - Confidence score: recalculate after updating statuses

- **services/admin-api/README.md**
  - Endpoints table: verify POST `/admin/users`, GET `/admin/users`, GET `/admin/users/{user_id}`, GET `/admin/users/email/{email}` request/response shapes match what the test sent and received
  - Token generation endpoint: verify POST `/admin/users/{user_id}/tokens?scopes=bot,tx,browser&name=label` returns a token with the expected scopes format
  - Auth middleware: verify `X-Admin-API-Key` header name and HMAC comparison behavior match test observations (401 vs 403 distinction)
  - Environment variables table: verify `ADMIN_API_TOKEN`, `DB_HOST`, `DB_PORT`, etc. match what the running container actually uses (from `docker exec printenv`)

- **services/api-gateway/README.md**
  - Bot Management endpoints: verify POST `/bots`, GET `/bots/status` request/response match what the test exercised
  - Admin proxy: verify `/admin/{path}` forwarding works for the admin endpoints the test called
  - Rate limiting section (Known Limitations #3): verify the documented tiers (API 120/min, admin 30/min, WS 20/min) match actual behavior observed during test
  - Token validation: verify the documented 60s Redis cache TTL matches actual caching behavior
  - Environment variables: verify `ADMIN_API_URL`, `MEETING_API_URL`, `TRANSCRIPTION_COLLECTOR_URL`, `MCP_URL` are all set and reachable

- **services/agent-api/README.md**
  - Health endpoint: verify GET `/health` response matches the documented `{"status": "ok"}` or equivalent
  - Environment variables: verify `AGENT_RUNTIME_PORT=8100`, `REDIS_URL`, `RUNTIME_API_URL` match actual container config
  - API Reference table: verify `/api/chat`, `/api/sessions`, `/api/workspace/*` endpoints exist and respond (the test checks health; note any endpoints that 404)

- **services/transcription-service/README.md**
  - API endpoint: verify POST `/v1/audio/transcriptions` accepts WAV file upload and returns `{text, language, segments}` as documented
  - Response format section: verify `language_probability`, `duration`, `segments` fields are present in actual response
  - Health check: verify GET `/health` response matches documented format (includes `gpu_available`)
  - Environment variables: verify `MODEL_SIZE`, `DEVICE`, `COMPUTE_TYPE`, `API_TOKEN` match running container config
