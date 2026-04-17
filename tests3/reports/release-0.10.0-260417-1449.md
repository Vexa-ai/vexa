# Release validation report — `0.10.0-260417-1449`

_Generated 2026-04-17T11:53:48.010347Z from `tests3/.state/reports/`._

## Scope status

**Release**: `260417-webhooks-dbpool` — Webhook delivery hardening (gateway injection + status webhooks + delivery

| Issue | Required modes | Status per proof | Verdict |
|-------|----------------|-------------------|---------|
| `webhook-gateway-injection` | compose | compose `webhooks/config`: ⬜ missing<br>compose `webhooks/inject`: ⬜ missing<br>helm `webhooks/inject`: ⬜ missing<br>compose `webhooks/spoof`: ⬜ missing<br>helm `webhooks/spoof`: ⬜ missing | **⬜ missing** |
| `webhook-status-fast-path` | compose | compose `webhooks/e2e_completion`: ⬜ missing<br>compose `webhooks/e2e_status`: ⬜ missing | **⬜ missing** |
| `db-pool-exhaustion` | compose, helm, lite | lite `DB_POOL_NO_EXHAUSTION`: ⬜ missing<br>compose `DB_POOL_NO_EXHAUSTION`: ⬜ missing<br>helm `DB_POOL_NO_EXHAUSTION`: ⬜ missing | **⬜ missing** |
| `transcripts-gone-after-stop` | compose, lite | lite `webhooks/e2e_completion`: ⬜ missing<br>compose `webhooks/e2e_completion`: ⬜ missing | **⬜ missing** |
| `recording-enabled-default` | compose | compose `BOT_RECORDING_ENABLED`: ⬜ missing<br>helm `BOT_RECORDING_ENABLED`: ⬜ missing | **⬜ missing** |

## Deployment coverage

| Mode | Image tag | Tests run | Passed | Failed |
|------|-----------|-----------|--------|--------|
| `compose` | `0.10.0-260417-1449` | 1 | 1 | 0 |
| `helm` | `—` | 0 | 0 | 0 |
| `lite` | `0.10.0-260417-1449` | 2 | 2 | 0 |

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
| bots-status-not-422 | GET /bots/status never returns 422 (schema stable under concurrent writes) | 5 | ⬜ missing | lite: check BOTS_STATUS_NOT_422 not found in any smoke-* report; compose: check BOTS_STATUS_NOT_422 not found in any smoke-* report; helm: check BOTS_STATUS_NOT_422 not found in any smoke-* report |
| removal | Container fully removed after DELETE /bots/... | 10 | ⬜ missing | compose: no report for test=containers |
| status-completed | Meeting.status=completed after stop (not failed/stuck) | 10 | ⬜ missing | compose: no report for test=containers; helm: no report for test=containers |
| graceful-leave | Bot leaves the meeting gracefully on stop (no force-kill by default) | 5 | ⬜ missing | lite: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping treated as completed, not failed; compose: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping treated as compl… |
| route-collision | No Starlette route collisions — /bots/{id} and /bots/{platform}/{native_id} do not clash | 5 | ⬜ missing | lite: smoke-static/ROUTE_COLLISION: bot detail route is /bots/id/{id}, not /bots/{id} which collides with /bots/status; compose: smoke-static/ROUTE_COLLISION: bot detail route is /bots/id/{id}, not… |
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
| cookie-security | HttpOnly + SameSite cookies on magic-link send/verify + admin-verify + nextauth | 10 | ⬜ missing | lite: smoke-static/SECURE_COOKIE_SEND_MAGIC_LINK: cookie Secure flag based on actual protocol, not NODE_ENV (send-magic-link); compose: smoke-static/SECURE_COOKIE_SEND_MAGIC_LINK: cookie Secure fla… |
| login-redirect | Magic-link click redirects to /meetings (not disabled /agent) | 5 | ⬜ missing | lite: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to disabled /agent page; compose: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to disabled /… |
| identity-no-fallback | /api/auth/me uses only the cookie for identity, never env fallback | 5 | ⬜ missing | lite: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses only cookie for identity, never falls back to env var; compose: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses only cookie for ident… |
| proxy-reachable | GET /api/vexa/meetings via cookie returns 200 | 10 | ⬜ missing | lite: no report for test=dashboard-auth; compose: no report for test=dashboard-auth; helm: no report for test=dashboard-auth |
| meetings-list | /api/vexa/meetings returns a meeting list through the dashboard proxy | 5 | ⬜ missing | compose: no report for test=dashboard-proxy; helm: no report for test=dashboard-proxy |
| pagination | limit/offset pagination works (no overlap between pages) | 5 | ⬜ missing | compose: no report for test=dashboard-proxy; helm: no report for test=dashboard-proxy |
| field-contract | Meeting records include native_meeting_id / platform_specific_id | 5 | ⬜ missing | compose: no report for test=dashboard-proxy; helm: no report for test=dashboard-proxy |
| transcript-proxy | Transcript reachable through dashboard proxy | 5 | ⬜ missing | compose: no report for test=dashboard-proxy; helm: no report for test=dashboard-proxy |
| bot-create-proxy | POST /api/vexa/bots reaches the gateway and creates a bot (or returns 403/409) | 5 | ⬜ missing | compose: no report for test=dashboard-proxy; helm: no report for test=dashboard-proxy |
| dashboard-up | Dashboard root page responds | 5 | ⬜ missing | lite: check DASHBOARD_UP not found in any smoke-* report; compose: check DASHBOARD_UP not found in any smoke-* report; helm: check DASHBOARD_UP not found in any smoke-* report |
| dashboard-ws-url | NEXT_PUBLIC_WS_URL is set — live updates can connect | 5 | ⬜ missing | lite: check DASHBOARD_WS_URL not found in any smoke-* report; compose: check DASHBOARD_WS_URL not found in any smoke-* report; helm: check DASHBOARD_WS_URL not found in any smoke-* report |
| dashboard-admin-key-valid | Dashboard's VEXA_ADMIN_API_KEY is accepted by admin-api (login path works) | 5 | ⬜ missing | lite: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard can authenticate to admin-api — user lookup and login will work; compose: check DASHBOARD_ADMIN_KEY_VALID not found in any smoke-* report; helm:… |

### `infrastructure` (0% / gate 100%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| gateway-up | API gateway responds to /admin/users via valid admin token | 10 | ⬜ missing | lite: check GATEWAY_UP not found in any smoke-* report; compose: check GATEWAY_UP not found in any smoke-* report; helm: check GATEWAY_UP not found in any smoke-* report |
| admin-api-up | admin-api responds with a valid list | 10 | ⬜ missing | lite: check ADMIN_API_UP not found in any smoke-* report; compose: check ADMIN_API_UP not found in any smoke-* report; helm: check ADMIN_API_UP not found in any smoke-* report |
| dashboard-up | dashboard root page responds | 10 | ⬜ missing | lite: check DASHBOARD_UP not found in any smoke-* report; compose: check DASHBOARD_UP not found in any smoke-* report; helm: check DASHBOARD_UP not found in any smoke-* report |
| runtime-api-up | runtime-api (bot orchestrator) is reachable / has ready replicas | 15 | ⬜ missing | lite: check RUNTIME_API_UP not found in any smoke-* report; compose: check RUNTIME_API_UP not found in any smoke-* report; helm: check RUNTIME_API_UP not found in any smoke-* report |
| transcription-up | transcription service /health returns ok + gpu_available | 15 | ⬜ missing | lite: check TRANSCRIPTION_UP not found in any smoke-* report; compose: check TRANSCRIPTION_UP not found in any smoke-* report; helm: check TRANSCRIPTION_UP not found in any smoke-* report |
| redis-up | Redis responds to PING | 10 | ⬜ missing | lite: check REDIS_UP not found in any smoke-* report; compose: check REDIS_UP not found in any smoke-* report; helm: check REDIS_UP not found in any smoke-* report |
| minio-up | MinIO is healthy / has ready replicas | 10 | ⬜ missing | compose: check MINIO_UP not found in any smoke-* report; helm: check MINIO_UP not found in any smoke-* report |
| db-schema | Database schema is aligned with the current model | 10 | ⬜ missing | lite: check DB_SCHEMA_ALIGNED not found in any smoke-* report; compose: check DB_SCHEMA_ALIGNED not found in any smoke-* report; helm: check DB_SCHEMA_ALIGNED not found in any smoke-* report |
| gateway-timeout | Gateway proxy timeout is ≥30s (prevents premature 504s under load) | 10 | ⬜ missing | lite: smoke-static/GATEWAY_TIMEOUT_ADEQUATE: API gateway HTTP client timeout >= 15s — browser session creation needs time; compose: smoke-static/GATEWAY_TIMEOUT_ADEQUATE: API gateway HTTP client ti… |

### `meeting-urls` (0% / gate 100%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| url-parser-exists | meeting-api has a URL parser module (url_parser.py) that handles platform detection | 10 | ⬜ missing | lite: smoke-static/URL_PARSER_EXISTS: MeetingCreate schema has parse_meeting_url — accepts meeting_url field directly; compose: smoke-static/URL_PARSER_EXISTS: MeetingCreate schema has parse_meetin… |
| gmeet-parsed | Google Meet URL (meet.google.com/xxx-xxxx-xxx) parses correctly | 15 | ⬜ missing | lite: check GMEET_URL_PARSED not found in any smoke-* report; compose: check GMEET_URL_PARSED not found in any smoke-* report; helm: check GMEET_URL_PARSED not found in any smoke-* report |
| invalid-rejected | Invalid meeting URL returns 400 (not 500) | 10 | ⬜ missing | lite: check INVALID_URL_REJECTED not found in any smoke-* report; compose: check INVALID_URL_REJECTED not found in any smoke-* report; helm: check INVALID_URL_REJECTED not found in any smoke-* report |
| teams-standard | Teams standard link (teams.microsoft.com/l/meetup-join/...) parses | 15 | ⬜ missing | lite: check TEAMS_URL_STANDARD not found in any smoke-* report; compose: check TEAMS_URL_STANDARD not found in any smoke-* report; helm: check TEAMS_URL_STANDARD not found in any smoke-* report |
| teams-shortlink | Teams shortlink (teams.live.com, teams.microsoft.com/meet) parses | 10 | ⬜ missing | lite: check TEAMS_URL_SHORTLINK not found in any smoke-* report; compose: check TEAMS_URL_SHORTLINK not found in any smoke-* report; helm: check TEAMS_URL_SHORTLINK not found in any smoke-* report |
| teams-channel | Teams channel meeting URL parses | 10 | ⬜ missing | lite: check TEAMS_URL_CHANNEL not found in any smoke-* report; compose: check TEAMS_URL_CHANNEL not found in any smoke-* report; helm: check TEAMS_URL_CHANNEL not found in any smoke-* report |
| teams-enterprise | Teams enterprise-tenant URL parses (custom domain) | 15 | ⬜ missing | lite: check TEAMS_URL_ENTERPRISE not found in any smoke-* report; compose: check TEAMS_URL_ENTERPRISE not found in any smoke-* report; helm: check TEAMS_URL_ENTERPRISE not found in any smoke-* report |
| teams-personal | Teams personal-account URL parses | 15 | ⬜ missing | lite: check TEAMS_URL_PERSONAL not found in any smoke-* report; compose: check TEAMS_URL_PERSONAL not found in any smoke-* report; helm: check TEAMS_URL_PERSONAL not found in any smoke-* report |

### `webhooks` (0% / gate 95%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| events-meeting-completed | meeting.completed fires on every bot exit (default-enabled) | 10 | ⬜ missing | compose: no report for test=webhooks |
| events-status-webhooks | Status-change webhooks fire when enabled via webhook_events (meeting.started / bot.failed / meeting.status_change) | 10 | ⬜ missing | compose: no report for test=webhooks |
| envelope-shape | Every webhook carries envelope: event_id, event_type, api_version, created_at, data | 10 | ⬜ missing | compose: no report for test=webhooks; helm: no report for test=webhooks |
| headers-hmac | X-Webhook-Signature = HMAC-SHA256(timestamp + '.' + payload) when secret is set | 10 | ⬜ missing | compose: no report for test=webhooks; helm: no report for test=webhooks |
| security-spoof-protection | Client-supplied X-User-Webhook-* headers cannot override stored config | 10 | ⬜ missing | compose: no report for test=webhooks; helm: no report for test=webhooks |
| security-secret-not-exposed | webhook_secret never appears in any API response (POST /bots, GET /bots/status) | 10 | ⬜ missing | compose: no report for test=webhooks; helm: no report for test=webhooks |
| security-payload-hygiene | Internal fields (secret, url, container ids, delivery state) stripped from webhook payloads | 5 | ⬜ missing | compose: no report for test=webhooks; helm: no report for test=webhooks |
| flow-user-config | PUT /user/webhook persists webhook_url + webhook_secret + webhook_events to User.data | 10 | ⬜ missing | compose: no report for test=webhooks |
| flow-gateway-inject | Gateway injects validated webhook config into meeting.data on POST /bots | 15 | ⬜ missing | compose: no report for test=webhooks |
| reliability-db-pool | DB connection pool doesn't exhaust under repeated status requests | 10 | ⬜ missing | lite: check DB_POOL_NO_EXHAUSTION not found in any smoke-* report; compose: check DB_POOL_NO_EXHAUSTION not found in any smoke-* report; helm: check DB_POOL_NO_EXHAUSTION not found in any smoke-* r… |

## Raw test results

### `compose`

| Test | Status | Duration | Steps (pass / total) |
|------|:------:|---------:|---------------------:|
| `smoke-static` | ✅ pass | 5 ms | 23 / 24 |

### `helm`

| Test | Status | Duration | Steps (pass / total) |
|------|:------:|---------:|---------------------:|

### `lite`

| Test | Status | Duration | Steps (pass / total) |
|------|:------:|---------:|---------------------:|
| `smoke-env` | ✅ pass | 730 ms | 7 / 7 |
| `smoke-static` | ✅ pass | 6 ms | 23 / 24 |
