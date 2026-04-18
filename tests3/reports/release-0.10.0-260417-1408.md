# Release validation report — `0.10.0-260417-1408`

_Generated 2026-04-17T11:20:03.175149Z from `tests3/.state/reports/`._

## Scope status

**Release**: `260417-webhooks-dbpool` — Webhook delivery hardening (gateway injection + status webhooks + delivery

| Issue | Required modes | Status per proof | Verdict |
|-------|----------------|-------------------|---------|
| `webhook-gateway-injection` | compose | compose `webhooks/config`: ⬜ missing<br>compose `webhooks/inject`: ⬜ missing<br>helm `webhooks/inject`: ⬜ missing<br>compose `webhooks/spoof`: ⬜ missing<br>helm `webhooks/spoof`: ⬜ missing | **⬜ missing** |
| `webhook-status-fast-path` | compose | compose `webhooks/e2e_completion`: ⬜ missing<br>compose `webhooks/e2e_status`: ⬜ missing | **⬜ missing** |
| `db-pool-exhaustion` | compose, helm, lite | lite `DB_POOL_NO_EXHAUSTION`: ⬜ missing<br>compose `DB_POOL_NO_EXHAUSTION`: ⬜ missing<br>helm `DB_POOL_NO_EXHAUSTION`: ❌ fail | **❌ fail** |
| `transcripts-gone-after-stop` | compose, lite | lite `webhooks/e2e_completion`: ⬜ missing<br>compose `webhooks/e2e_completion`: ⬜ missing | **⬜ missing** |
| `recording-enabled-default` | compose | compose `BOT_RECORDING_ENABLED`: ⬜ missing<br>helm `BOT_RECORDING_ENABLED`: ⚠️ skip | **⬜ missing** |

## Deployment coverage

| Mode | Image tag | Tests run | Passed | Failed |
|------|-----------|-----------|--------|--------|
| `compose` | `—` | 0 | 0 | 0 |
| `helm` | `0.10.0-260417-1408` | 5 | 2 | 3 |
| `lite` | `—` | 0 | 0 | 0 |

## Feature confidence

| Feature | Confidence | Gate | Status |
|---------|-----------:|-----:|:-------|
| `bot-lifecycle` | **0%** | 90% | ❌ below gate |
| `dashboard` | **0%** | 90% | ❌ below gate |
| `infrastructure` | **0%** | 100% | ❌ below gate |
| `meeting-urls` | **0%** | 100% | ❌ below gate |
| `webhooks` | **0%** | 95% | ❌ below gate |

## DoD details

### `bot-lifecycle` (0% / gate 90%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| create-ok | POST /bots spawns a bot container and returns a bot id | 15 | ⬜ missing | compose: no report for test=containers; helm: no report for test=containers |
| create-alive | Bot process is running 10s after creation (not crash-looping) | 15 | ⬜ missing | compose: no report for test=containers; helm: no report for test=containers |
| bots-status-not-422 | GET /bots/status never returns 422 (schema stable under concurrent writes) | 5 | ❌ fail | lite: check BOTS_STATUS_NOT_422 not found in any smoke-* report; compose: check BOTS_STATUS_NOT_422 not found in any smoke-* report; helm: smoke-contract/BOTS_STATUS_NOT_422: HTTP 401 (expected 200) |
| removal | Container fully removed after DELETE /bots/... | 10 | ⬜ missing | compose: no report for test=containers |
| status-completed | Meeting.status=completed after stop (not failed/stuck) | 10 | ⬜ missing | compose: no report for test=containers; helm: no report for test=containers |
| graceful-leave | Bot leaves the meeting gracefully on stop (no force-kill by default) | 5 | ⬜ missing | lite: check GRACEFUL_LEAVE not found in any smoke-* report; compose: check GRACEFUL_LEAVE not found in any smoke-* report; helm: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping tr… |
| route-collision | No Starlette route collisions — /bots/{id} and /bots/{platform}/{native_id} do not clash | 5 | ⬜ missing | lite: check ROUTE_COLLISION not found in any smoke-* report; compose: check ROUTE_COLLISION not found in any smoke-* report; helm: smoke-static/ROUTE_COLLISION: bot detail route is /bots/id/{id}, n… |
| timeout-stop | Bot auto-stops after automatic_leave timeout (no_one_joined_timeout) | 10 | ⬜ missing | compose: no report for test=containers |
| concurrency-slot | Concurrent-bot slot released immediately on stop — next create succeeds | 10 | ⬜ missing | compose: no report for test=containers; helm: no report for test=containers |
| no-orphans | No zombie/exited bot containers left after a lifecycle run | 10 | ⬜ missing | compose: no report for test=containers |
| status-webhooks-fire | Status-change webhooks fire for every transition when enabled in webhook_events | 5 | ⬜ missing | compose: no report for test=webhooks |

### `dashboard` (0% / gate 90%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| login-flow | POST /api/auth/send-magic-link → 200 + success=true + sets vexa-token cookie | 10 | ⬜ missing | lite: no report for test=dashboard-auth; compose: no report for test=dashboard-auth; helm: no report for test=dashboard-auth |
| cookie-flags | vexa-token cookie Secure flag matches deployment (Secure iff https) | 10 | ⬜ missing | lite: no report for test=dashboard-auth; compose: no report for test=dashboard-auth; helm: no report for test=dashboard-auth |
| identity-me | GET /api/auth/me returns logged-in user's email (never falls back to env) | 10 | ⬜ missing | lite: no report for test=dashboard-auth; compose: no report for test=dashboard-auth; helm: no report for test=dashboard-auth |
| cookie-security | HttpOnly + SameSite cookies on magic-link send/verify + admin-verify + nextauth | 10 | ⬜ missing | lite: check SECURE_COOKIE_SEND_MAGIC_LINK not found in any smoke-* report; compose: check SECURE_COOKIE_SEND_MAGIC_LINK not found in any smoke-* report; helm: smoke-static/SECURE_COOKIE_SEND_MAGIC_… |
| login-redirect | Magic-link click redirects to /meetings (not disabled /agent) | 5 | ⬜ missing | lite: check LOGIN_REDIRECT not found in any smoke-* report; compose: check LOGIN_REDIRECT not found in any smoke-* report; helm: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), … |
| identity-no-fallback | /api/auth/me uses only the cookie for identity, never env fallback | 5 | ⬜ missing | lite: check IDENTITY_NO_FALLBACK not found in any smoke-* report; compose: check IDENTITY_NO_FALLBACK not found in any smoke-* report; helm: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses onl… |
| proxy-reachable | GET /api/vexa/meetings via cookie returns 200 | 10 | ⬜ missing | lite: no report for test=dashboard-auth; compose: no report for test=dashboard-auth; helm: no report for test=dashboard-auth |
| meetings-list | /api/vexa/meetings returns a meeting list through the dashboard proxy | 5 | ⬜ missing | compose: no report for test=dashboard-proxy; helm: no report for test=dashboard-proxy |
| pagination | limit/offset pagination works (no overlap between pages) | 5 | ⬜ missing | compose: no report for test=dashboard-proxy; helm: no report for test=dashboard-proxy |
| field-contract | Meeting records include native_meeting_id / platform_specific_id | 5 | ⬜ missing | compose: no report for test=dashboard-proxy; helm: no report for test=dashboard-proxy |
| transcript-proxy | Transcript reachable through dashboard proxy | 5 | ⬜ missing | compose: no report for test=dashboard-proxy; helm: no report for test=dashboard-proxy |
| bot-create-proxy | POST /api/vexa/bots reaches the gateway and creates a bot (or returns 403/409) | 5 | ⬜ missing | compose: no report for test=dashboard-proxy; helm: no report for test=dashboard-proxy |
| dashboard-up | Dashboard root page responds | 5 | ⬜ missing | lite: check DASHBOARD_UP not found in any smoke-* report; compose: check DASHBOARD_UP not found in any smoke-* report; helm: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI |
| dashboard-ws-url | NEXT_PUBLIC_WS_URL is set — live updates can connect | 5 | ⬜ missing | lite: check DASHBOARD_WS_URL not found in any smoke-* report; compose: check DASHBOARD_WS_URL not found in any smoke-* report; helm: smoke-health/DASHBOARD_WS_URL: wss://dashboard.staging.vexa.ai/ws |
| dashboard-admin-key-valid | Dashboard's VEXA_ADMIN_API_KEY is accepted by admin-api (login path works) | 5 | ⬜ missing | lite: check DASHBOARD_ADMIN_KEY_VALID not found in any smoke-* report; compose: check DASHBOARD_ADMIN_KEY_VALID not found in any smoke-* report; helm: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard… |

### `infrastructure` (0% / gate 100%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| gateway-up | API gateway responds to /admin/users via valid admin token | 10 | ⬜ missing | lite: check GATEWAY_UP not found in any smoke-* report; compose: check GATEWAY_UP not found in any smoke-* report; helm: smoke-health/GATEWAY_UP: API gateway accepts connections — all client reques… |
| admin-api-up | admin-api responds with a valid list | 10 | ⬜ missing | lite: check ADMIN_API_UP not found in any smoke-* report; compose: check ADMIN_API_UP not found in any smoke-* report; helm: smoke-health/ADMIN_API_UP: admin-api responds with valid token — user ma… |
| dashboard-up | dashboard root page responds | 10 | ⬜ missing | lite: check DASHBOARD_UP not found in any smoke-* report; compose: check DASHBOARD_UP not found in any smoke-* report; helm: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI |
| runtime-api-up | runtime-api (bot orchestrator) is reachable / has ready replicas | 15 | ❌ fail | lite: check RUNTIME_API_UP not found in any smoke-* report; compose: check RUNTIME_API_UP not found in any smoke-* report; helm: smoke-health/RUNTIME_API_UP: runtime-api: 0 ready replicas |
| transcription-up | transcription service /health returns ok + gpu_available | 15 | ⬜ missing | lite: check TRANSCRIPTION_UP not found in any smoke-* report; compose: check TRANSCRIPTION_UP not found in any smoke-* report; helm: smoke-health/TRANSCRIPTION_UP: transcription service responds — … |
| redis-up | Redis responds to PING | 10 | ❌ fail | lite: check REDIS_UP not found in any smoke-* report; compose: check REDIS_UP not found in any smoke-* report; helm: smoke-health/REDIS_UP: redis-cli ping:  |
| minio-up | MinIO is healthy / has ready replicas | 10 | ❌ fail | compose: check MINIO_UP not found in any smoke-* report; helm: smoke-health/MINIO_UP: minio: 0 ready replicas |
| db-schema | Database schema is aligned with the current model | 10 | ⬜ missing | lite: check DB_SCHEMA_ALIGNED not found in any smoke-* report; compose: check DB_SCHEMA_ALIGNED not found in any smoke-* report; helm: smoke-health/DB_SCHEMA_ALIGNED: all required columns present |
| gateway-timeout | Gateway proxy timeout is ≥30s (prevents premature 504s under load) | 10 | ⬜ missing | lite: check GATEWAY_TIMEOUT_ADEQUATE not found in any smoke-* report; compose: check GATEWAY_TIMEOUT_ADEQUATE not found in any smoke-* report; helm: smoke-static/GATEWAY_TIMEOUT_ADEQUATE: API gatew… |

### `meeting-urls` (0% / gate 100%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| url-parser-exists | meeting-api has a URL parser module (url_parser.py) that handles platform detection | 10 | ⬜ missing | lite: check URL_PARSER_EXISTS not found in any smoke-* report; compose: check URL_PARSER_EXISTS not found in any smoke-* report; helm: smoke-static/URL_PARSER_EXISTS: MeetingCreate schema has parse… |
| gmeet-parsed | Google Meet URL (meet.google.com/xxx-xxxx-xxx) parses correctly | 15 | ❌ fail | lite: check GMEET_URL_PARSED not found in any smoke-* report; compose: check GMEET_URL_PARSED not found in any smoke-* report; helm: smoke-contract/GMEET_URL_PARSED: HTTP 401 (expected one of [200,… |
| invalid-rejected | Invalid meeting URL returns 400 (not 500) | 10 | ❌ fail | lite: check INVALID_URL_REJECTED not found in any smoke-* report; compose: check INVALID_URL_REJECTED not found in any smoke-* report; helm: smoke-contract/INVALID_URL_REJECTED: HTTP 401 (expected … |
| teams-standard | Teams standard link (teams.microsoft.com/l/meetup-join/...) parses | 15 | ❌ fail | lite: check TEAMS_URL_STANDARD not found in any smoke-* report; compose: check TEAMS_URL_STANDARD not found in any smoke-* report; helm: smoke-contract/TEAMS_URL_STANDARD: HTTP 401 (expected one of… |
| teams-shortlink | Teams shortlink (teams.live.com, teams.microsoft.com/meet) parses | 10 | ❌ fail | lite: check TEAMS_URL_SHORTLINK not found in any smoke-* report; compose: check TEAMS_URL_SHORTLINK not found in any smoke-* report; helm: smoke-contract/TEAMS_URL_SHORTLINK: HTTP 401 (expected one… |
| teams-channel | Teams channel meeting URL parses | 10 | ❌ fail | lite: check TEAMS_URL_CHANNEL not found in any smoke-* report; compose: check TEAMS_URL_CHANNEL not found in any smoke-* report; helm: smoke-contract/TEAMS_URL_CHANNEL: HTTP 401 (expected one of [2… |
| teams-enterprise | Teams enterprise-tenant URL parses (custom domain) | 15 | ❌ fail | lite: check TEAMS_URL_ENTERPRISE not found in any smoke-* report; compose: check TEAMS_URL_ENTERPRISE not found in any smoke-* report; helm: smoke-contract/TEAMS_URL_ENTERPRISE: HTTP 401 (expected … |
| teams-personal | Teams personal-account URL parses | 15 | ❌ fail | lite: check TEAMS_URL_PERSONAL not found in any smoke-* report; compose: check TEAMS_URL_PERSONAL not found in any smoke-* report; helm: smoke-contract/TEAMS_URL_PERSONAL: HTTP 401 (expected one of… |

### `webhooks` (0% / gate 95%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| events-meeting-completed | meeting.completed fires on every bot exit (default-enabled) | 10 | ⬜ missing | compose: no report for test=webhooks |
| events-status-webhooks | Status-change webhooks fire when enabled via webhook_events (meeting.started / bot.failed / meeting.status_change) | 10 | ⬜ missing | compose: no report for test=webhooks |
| envelope-shape | Every webhook carries envelope: event_id, event_type, api_version, created_at, data | 10 | ⬜ missing | compose: no report for test=webhooks; helm: report has no step=envelope |
| headers-hmac | X-Webhook-Signature = HMAC-SHA256(timestamp + '.' + payload) when secret is set | 10 | ⬜ missing | compose: no report for test=webhooks; helm: report has no step=hmac |
| security-spoof-protection | Client-supplied X-User-Webhook-* headers cannot override stored config | 10 | ⬜ missing | compose: no report for test=webhooks; helm: report has no step=spoof |
| security-secret-not-exposed | webhook_secret never appears in any API response (POST /bots, GET /bots/status) | 10 | ⬜ missing | compose: no report for test=webhooks; helm: report has no step=no_leak_response |
| security-payload-hygiene | Internal fields (secret, url, container ids, delivery state) stripped from webhook payloads | 5 | ⬜ missing | compose: no report for test=webhooks; helm: report has no step=no_leak_payload |
| flow-user-config | PUT /user/webhook persists webhook_url + webhook_secret + webhook_events to User.data | 10 | ⬜ missing | compose: no report for test=webhooks |
| flow-gateway-inject | Gateway injects validated webhook config into meeting.data on POST /bots | 15 | ⬜ missing | compose: no report for test=webhooks |
| reliability-db-pool | DB connection pool doesn't exhaust under repeated status requests | 10 | ❌ fail | lite: check DB_POOL_NO_EXHAUSTION not found in any smoke-* report; compose: check DB_POOL_NO_EXHAUSTION not found in any smoke-* report; helm: smoke-contract/DB_POOL_NO_EXHAUSTION: 10/10 requests f… |

## Raw test results

### `compose`

| Test | Status | Duration | Steps (pass / total) |
|------|:------:|---------:|---------------------:|

### `helm`

| Test | Status | Duration | Steps (pass / total) |
|------|:------:|---------:|---------------------:|
| `smoke-contract` | ❌ fail | 21149 ms | 6 / 23 |
| `smoke-env` | ✅ pass | 3577 ms | 7 / 7 |
| `smoke-health` | ❌ fail | 17769 ms | 10 / 15 |
| `smoke-static` | ✅ pass | 212 ms | 23 / 24 |
| `webhooks` | ❌ fail | 625 ms | 0 / 0 |

### `lite`

| Test | Status | Duration | Steps (pass / total) |
|------|:------:|---------:|---------------------:|
