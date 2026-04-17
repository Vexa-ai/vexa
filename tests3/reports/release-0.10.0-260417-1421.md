# Release validation report — `0.10.0-260417-1421`

_Generated 2026-04-17T11:26:36.422202Z from `tests3/.state/reports/`._

## Scope status

**Release**: `260417-webhooks-dbpool` — Webhook delivery hardening (gateway injection + status webhooks + delivery

| Issue | Required modes | Status per proof | Verdict |
|-------|----------------|-------------------|---------|
| `webhook-gateway-injection` | compose | compose `webhooks/config`: ✅ pass<br>compose `webhooks/inject`: ✅ pass<br>helm `webhooks/inject`: ⬜ missing<br>compose `webhooks/spoof`: ⚠️ skip<br>helm `webhooks/spoof`: ⬜ missing | **⚠️ skip** |
| `webhook-status-fast-path` | compose | compose `webhooks/e2e_completion`: ✅ pass<br>compose `webhooks/e2e_status`: ✅ pass | **✅ pass** |
| `db-pool-exhaustion` | compose, helm, lite | lite `DB_POOL_NO_EXHAUSTION`: ❌ fail<br>compose `DB_POOL_NO_EXHAUSTION`: ❌ fail<br>helm `DB_POOL_NO_EXHAUSTION`: ❌ fail | **❌ fail** |
| `transcripts-gone-after-stop` | compose, lite | lite `webhooks/e2e_completion`: ⬜ missing<br>compose `webhooks/e2e_completion`: ✅ pass | **⬜ missing** |
| `recording-enabled-default` | compose | compose `BOT_RECORDING_ENABLED`: ⚠️ skip<br>helm `BOT_RECORDING_ENABLED`: ⚠️ skip | **⚠️ skip** |

## Deployment coverage

| Mode | Image tag | Tests run | Passed | Failed |
|------|-----------|-----------|--------|--------|
| `compose` | `0.10.0-260417-1421` | 8 | 2 | 6 |
| `helm` | `0.10.0-260417-1408` | 5 | 2 | 3 |
| `lite` | `0.10.0-260417-1421` | 7 | 4 | 3 |

## Feature confidence

| Feature | Confidence | Gate | Status |
|---------|-----------:|-----:|:-------|
| `bot-lifecycle` | **15%** | 90% | ❌ below gate |
| `dashboard` | **20%** | 90% | ❌ below gate |
| `infrastructure` | **35%** | 100% | ❌ below gate |
| `meeting-urls` | **10%** | 100% | ❌ below gate |
| `webhooks` | **45%** | 95% | ❌ below gate |

## DoD details

### `bot-lifecycle` (15% / gate 90%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| create-ok | POST /bots spawns a bot container and returns a bot id | 15 | ⬜ missing | compose: report has no step=create; helm: no report for test=containers |
| create-alive | Bot process is running 10s after creation (not crash-looping) | 15 | ⬜ missing | compose: report has no step=alive; helm: no report for test=containers |
| bots-status-not-422 | GET /bots/status never returns 422 (schema stable under concurrent writes) | 5 | ❌ fail | lite: smoke-contract/BOTS_STATUS_NOT_422: HTTP 401 (expected 200); compose: smoke-contract/BOTS_STATUS_NOT_422: HTTP 0 (expected 200); helm: smoke-contract/BOTS_STATUS_NOT_422: HTTP 401 (expected 200) |
| removal | Container fully removed after DELETE /bots/... | 10 | ⬜ missing | compose: report has no step=removal |
| status-completed | Meeting.status=completed after stop (not failed/stuck) | 10 | ⬜ missing | compose: report has no step=status_completed; helm: no report for test=containers |
| graceful-leave | Bot leaves the meeting gracefully on stop (no force-kill by default) | 5 | ✅ pass | lite: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping treated as completed, not failed; compose: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping treated as compl… |
| route-collision | No Starlette route collisions — /bots/{id} and /bots/{platform}/{native_id} do not clash | 5 | ✅ pass | lite: smoke-static/ROUTE_COLLISION: bot detail route is /bots/id/{id}, not /bots/{id} which collides with /bots/status; compose: smoke-static/ROUTE_COLLISION: bot detail route is /bots/id/{id}, not… |
| timeout-stop | Bot auto-stops after automatic_leave timeout (no_one_joined_timeout) | 10 | ⬜ missing | compose: report has no step=timeout_stop |
| concurrency-slot | Concurrent-bot slot released immediately on stop — next create succeeds | 10 | ⬜ missing | compose: report has no step=concurrency_slot; helm: no report for test=containers |
| no-orphans | No zombie/exited bot containers left after a lifecycle run | 10 | ⬜ missing | compose: report has no step=no_orphans |
| status-webhooks-fire | Status-change webhooks fire for every transition when enabled in webhook_events | 5 | ✅ pass | compose: webhooks/e2e_status: 1 status-change webhook(s) fired: meeting.completed |

### `dashboard` (20% / gate 90%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| login-flow | POST /api/auth/send-magic-link → 200 + success=true + sets vexa-token cookie | 10 | ⬜ missing | lite: dashboard-auth/login: 200 + success=true; compose: report has no step=login; helm: no report for test=dashboard-auth |
| cookie-flags | vexa-token cookie Secure flag matches deployment (Secure iff https) | 10 | ⬜ missing | lite: dashboard-auth/cookie_flags: flags correct for http; compose: report has no step=cookie_flags; helm: no report for test=dashboard-auth |
| identity-me | GET /api/auth/me returns logged-in user's email (never falls back to env) | 10 | ⬜ missing | lite: dashboard-auth/identity: /me returns test@vexa.ai; compose: report has no step=identity; helm: no report for test=dashboard-auth |
| cookie-security | HttpOnly + SameSite cookies on magic-link send/verify + admin-verify + nextauth | 10 | ✅ pass | lite: smoke-static/SECURE_COOKIE_SEND_MAGIC_LINK: cookie Secure flag based on actual protocol, not NODE_ENV (send-magic-link); compose: smoke-static/SECURE_COOKIE_SEND_MAGIC_LINK: cookie Secure fla… |
| login-redirect | Magic-link click redirects to /meetings (not disabled /agent) | 5 | ✅ pass | lite: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to disabled /agent page; compose: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to disabled /… |
| identity-no-fallback | /api/auth/me uses only the cookie for identity, never env fallback | 5 | ✅ pass | lite: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses only cookie for identity, never falls back to env var; compose: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses only cookie for ident… |
| proxy-reachable | GET /api/vexa/meetings via cookie returns 200 | 10 | ⬜ missing | lite: dashboard-auth/proxy_reachable: /api/vexa/meetings → 200; compose: report has no step=proxy_reachable; helm: no report for test=dashboard-auth |
| meetings-list | /api/vexa/meetings returns a meeting list through the dashboard proxy | 5 | ⬜ missing | compose: report has no step=meetings_list; helm: no report for test=dashboard-proxy |
| pagination | limit/offset pagination works (no overlap between pages) | 5 | ⬜ missing | compose: report has no step=pagination; helm: no report for test=dashboard-proxy |
| field-contract | Meeting records include native_meeting_id / platform_specific_id | 5 | ⬜ missing | compose: report has no step=field_contract; helm: no report for test=dashboard-proxy |
| transcript-proxy | Transcript reachable through dashboard proxy | 5 | ⬜ missing | compose: report has no step=transcript_proxy; helm: no report for test=dashboard-proxy |
| bot-create-proxy | POST /api/vexa/bots reaches the gateway and creates a bot (or returns 403/409) | 5 | ⬜ missing | compose: report has no step=bot_create_proxy; helm: no report for test=dashboard-proxy |
| dashboard-up | Dashboard root page responds | 5 | ❌ fail | lite: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; compose: smoke-health/DASHBOARD_UP: HTTP 0 (expected 200); helm: smoke-health/DASHBOARD_UP: dashboard serves pages … |
| dashboard-ws-url | NEXT_PUBLIC_WS_URL is set — live updates can connect | 5 | ❌ fail | lite: smoke-health/DASHBOARD_WS_URL: ws://localhost:3000/ws; compose: smoke-health/DASHBOARD_WS_URL: HTTP 0 from http://localhost:3001/api/config; helm: smoke-health/DASHBOARD_WS_URL: wss://dashboa… |
| dashboard-admin-key-valid | Dashboard's VEXA_ADMIN_API_KEY is accepted by admin-api (login path works) | 5 | ❌ fail | lite: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard can authenticate to admin-api — user lookup and login will work; compose: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard: VEXA_ADMIN_API_KEY not… |

### `infrastructure` (35% / gate 100%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| gateway-up | API gateway responds to /admin/users via valid admin token | 10 | ❌ fail | lite: smoke-health/GATEWAY_UP: API gateway accepts connections — all client requests can reach backend; compose: smoke-health/GATEWAY_UP: HTTP 0 (expected 200); helm: smoke-health/GATEWAY_UP: API g… |
| admin-api-up | admin-api responds with a valid list | 10 | ❌ fail | lite: smoke-health/ADMIN_API_UP: admin-api responds with valid token — user management and login work; compose: smoke-health/ADMIN_API_UP: HTTP 0 (expected 200); helm: smoke-health/ADMIN_API_UP: ad… |
| dashboard-up | dashboard root page responds | 10 | ❌ fail | lite: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; compose: smoke-health/DASHBOARD_UP: HTTP 0 (expected 200); helm: smoke-health/DASHBOARD_UP: dashboard serves pages … |
| runtime-api-up | runtime-api (bot orchestrator) is reachable / has ready replicas | 15 | ❌ fail | lite: smoke-health/RUNTIME_API_UP: runtime-api responds — bot container lifecycle management works; compose: smoke-health/RUNTIME_API_UP: HTTP 0 from localhost:8090/health; helm: smoke-health/RUNTI… |
| transcription-up | transcription service /health returns ok + gpu_available | 15 | ✅ pass | lite: smoke-health/TRANSCRIPTION_UP: transcription service responds — audio can be converted to text; compose: smoke-health/TRANSCRIPTION_UP: transcription service responds — audio can be converted… |
| redis-up | Redis responds to PING | 10 | ❌ fail | lite: smoke-health/REDIS_UP: Redis responds to PING — WebSocket pub/sub, session state, and caching work; compose: smoke-health/REDIS_UP: redis-cli ping: ; helm: smoke-health/REDIS_UP: redis-cli pi… |
| minio-up | MinIO is healthy / has ready replicas | 10 | ❌ fail | compose: smoke-health/MINIO_UP: HTTP 0 from localhost:9000; helm: smoke-health/MINIO_UP: minio: 0 ready replicas |
| db-schema | Database schema is aligned with the current model | 10 | ✅ pass | lite: smoke-health/DB_SCHEMA_ALIGNED: all required columns present; compose: smoke-health/DB_SCHEMA_ALIGNED: all required columns present; helm: smoke-health/DB_SCHEMA_ALIGNED: all required columns… |
| gateway-timeout | Gateway proxy timeout is ≥30s (prevents premature 504s under load) | 10 | ✅ pass | lite: smoke-static/GATEWAY_TIMEOUT_ADEQUATE: API gateway HTTP client timeout >= 15s — browser session creation needs time; compose: smoke-static/GATEWAY_TIMEOUT_ADEQUATE: API gateway HTTP client ti… |

### `meeting-urls` (10% / gate 100%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| url-parser-exists | meeting-api has a URL parser module (url_parser.py) that handles platform detection | 10 | ✅ pass | lite: smoke-static/URL_PARSER_EXISTS: MeetingCreate schema has parse_meeting_url — accepts meeting_url field directly; compose: smoke-static/URL_PARSER_EXISTS: MeetingCreate schema has parse_meetin… |
| gmeet-parsed | Google Meet URL (meet.google.com/xxx-xxxx-xxx) parses correctly | 15 | ❌ fail | lite: smoke-contract/GMEET_URL_PARSED: HTTP 401 (expected one of [200, 201, 202, 403, 409, 500]); compose: smoke-contract/GMEET_URL_PARSED: HTTP 0 (expected one of [200, 201, 202, 403, 409, 500]); … |
| invalid-rejected | Invalid meeting URL returns 400 (not 500) | 10 | ❌ fail | lite: smoke-contract/INVALID_URL_REJECTED: HTTP 401 (expected one of [400, 422]); compose: smoke-contract/INVALID_URL_REJECTED: HTTP 0 (expected one of [400, 422]); helm: smoke-contract/INVALID_URL… |
| teams-standard | Teams standard link (teams.microsoft.com/l/meetup-join/...) parses | 15 | ❌ fail | lite: smoke-contract/TEAMS_URL_STANDARD: HTTP 401 (expected one of [200, 201, 202, 403, 409, 500]); compose: smoke-contract/TEAMS_URL_STANDARD: HTTP 0 (expected one of [200, 201, 202, 403, 409, 500… |
| teams-shortlink | Teams shortlink (teams.live.com, teams.microsoft.com/meet) parses | 10 | ❌ fail | lite: smoke-contract/TEAMS_URL_SHORTLINK: HTTP 401 (expected one of [200, 201, 202, 403, 409, 500]); compose: smoke-contract/TEAMS_URL_SHORTLINK: HTTP 0 (expected one of [200, 201, 202, 403, 409, 5… |
| teams-channel | Teams channel meeting URL parses | 10 | ❌ fail | lite: smoke-contract/TEAMS_URL_CHANNEL: HTTP 401 (expected one of [200, 201, 202, 403, 409, 422, 500]); compose: smoke-contract/TEAMS_URL_CHANNEL: HTTP 0 (expected one of [200, 201, 202, 403, 409, … |
| teams-enterprise | Teams enterprise-tenant URL parses (custom domain) | 15 | ❌ fail | lite: smoke-contract/TEAMS_URL_ENTERPRISE: HTTP 401 (expected one of [200, 201, 202, 403, 409, 500]); compose: smoke-contract/TEAMS_URL_ENTERPRISE: HTTP 0 (expected one of [200, 201, 202, 403, 409,… |
| teams-personal | Teams personal-account URL parses | 15 | ❌ fail | lite: smoke-contract/TEAMS_URL_PERSONAL: HTTP 401 (expected one of [200, 201, 202, 403, 409, 500]); compose: smoke-contract/TEAMS_URL_PERSONAL: HTTP 0 (expected one of [200, 201, 202, 403, 409, 500… |

### `webhooks` (45% / gate 95%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| events-meeting-completed | meeting.completed fires on every bot exit (default-enabled) | 10 | ✅ pass | compose: webhooks/e2e_completion: webhook_delivery.status=delivered |
| events-status-webhooks | Status-change webhooks fire when enabled via webhook_events (meeting.started / bot.failed / meeting.status_change) | 10 | ✅ pass | compose: webhooks/e2e_status: 1 status-change webhook(s) fired: meeting.completed |
| envelope-shape | Every webhook carries envelope: event_id, event_type, api_version, created_at, data | 10 | ⬜ missing | compose: webhooks/envelope: event_id, event_type, api_version, created_at, data present; helm: report has no step=envelope |
| headers-hmac | X-Webhook-Signature = HMAC-SHA256(timestamp + '.' + payload) when secret is set | 10 | ⬜ missing | compose: webhooks/hmac: HMAC-SHA256 64-char digest; helm: report has no step=hmac |
| security-spoof-protection | Client-supplied X-User-Webhook-* headers cannot override stored config | 10 | ⬜ missing | compose: webhooks/spoof: bot creation for spoof test failed (HTTP 403); helm: report has no step=spoof |
| security-secret-not-exposed | webhook_secret never appears in any API response (POST /bots, GET /bots/status) | 10 | ⬜ missing | compose: webhooks/no_leak_response: webhook_secret not in /bots/status response; helm: report has no step=no_leak_response |
| security-payload-hygiene | Internal fields (secret, url, container ids, delivery state) stripped from webhook payloads | 5 | ⬜ missing | compose: webhooks/no_leak_payload: internal fields stripped; user fields preserved; helm: report has no step=no_leak_payload |
| flow-user-config | PUT /user/webhook persists webhook_url + webhook_secret + webhook_events to User.data | 10 | ✅ pass | compose: webhooks/config: user webhook set via PUT /user/webhook |
| flow-gateway-inject | Gateway injects validated webhook config into meeting.data on POST /bots | 15 | ✅ pass | compose: webhooks/inject: gateway injected webhook_url=https://httpbin.org/post into meeting.data |
| reliability-db-pool | DB connection pool doesn't exhaust under repeated status requests | 10 | ❌ fail | lite: smoke-contract/DB_POOL_NO_EXHAUSTION: 10/10 requests failed — likely DB pool exhaustion; compose: smoke-contract/DB_POOL_NO_EXHAUSTION: 10/10 requests failed — likely DB pool exhaustion; helm… |

## Raw test results

### `compose`

| Test | Status | Duration | Steps (pass / total) |
|------|:------:|---------:|---------------------:|
| `containers` | ❌ fail | 169 ms | 0 / 0 |
| `dashboard-auth` | ❌ fail | 44 ms | 0 / 0 |
| `dashboard-proxy` | ❌ fail | 43 ms | 0 / 0 |
| `smoke-contract` | ❌ fail | 1700 ms | 5 / 23 |
| `smoke-env` | ❌ fail | 129 ms | 0 / 7 |
| `smoke-health` | ❌ fail | 682 ms | 2 / 15 |
| `smoke-static` | ✅ pass | 4 ms | 23 / 24 |
| `webhooks` | ✅ pass | 35477 ms | 8 / 9 |

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
| `containers` | ❌ fail | 174 ms | 0 / 0 |
| `dashboard-auth` | ✅ pass | 506 ms | 4 / 4 |
| `smoke-contract` | ❌ fail | 7981 ms | 6 / 23 |
| `smoke-env` | ✅ pass | 675 ms | 7 / 7 |
| `smoke-health` | ✅ pass | 4731 ms | 10 / 15 |
| `smoke-static` | ✅ pass | 5 ms | 23 / 24 |
| `webhooks` | ❌ fail | 148 ms | 0 / 0 |
