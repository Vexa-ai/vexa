# Release validation report — `0.10.0-260419-1140`

_Generated 2026-04-19T11:16:32.766578Z from `tests3/.state/reports/`._

## Deployment coverage

| Mode | Image tag | Tests run | Passed | Failed |
|------|-----------|-----------|--------|--------|
| `compose` | `—` | 8 | 7 | 1 |
| `helm` | `0.10.0-260419-1140` | 8 | 8 | 0 |
| `lite` | `0.10.0-260419-0052` | 7 | 6 | 1 |

## Feature confidence

| Feature | Confidence | Gate | Status |
|---------|-----------:|-----:|:-------|
| `auth-and-limits` | **0%** | 0% | ✅ pass |
| `authenticated-meetings` | **0%** | 0% | ✅ pass |
| `bot-lifecycle` | **90%** | 90% | ✅ pass |
| `browser-session` | **0%** | 0% | ✅ pass |
| `container-lifecycle` | **0%** | 0% | ✅ pass |
| `dashboard` | **95%** | 90% | ✅ pass |
| `infrastructure` | **100%** | 100% | ✅ pass |
| `meeting-chat` | **0%** | 0% | ✅ pass |
| `meeting-urls` | **100%** | 100% | ✅ pass |
| `post-meeting-transcription` | **0%** | 0% | ✅ pass |
| `realtime-transcription` | **0%** | 0% | ✅ pass |
| `realtime-transcription/gmeet` | **0%** | 0% | ✅ pass |
| `realtime-transcription/msteams` | **0%** | 0% | ✅ pass |
| `realtime-transcription/zoom` | **0%** | 0% | ✅ pass |
| `remote-browser` | **0%** | 0% | ✅ pass |
| `speaking-bot` | **0%** | 0% | ✅ pass |
| `webhooks` | **100%** | 95% | ✅ pass |

## DoD details

### `auth-and-limits` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `authenticated-meetings` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `bot-lifecycle` (90% / gate 90%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| create-ok | POST /bots spawns a bot container and returns a bot id | 15 | ✅ pass | helm: containers/create: bot 8 created |
| create-alive | Bot process is running 10s after creation (not crash-looping) | 15 | ✅ pass | helm: containers/alive: bot process running after 10s |
| bots-status-not-422 | GET /bots/status never returns 422 (schema stable under concurrent writes) | 5 | ✅ pass | lite: smoke-contract/BOTS_STATUS_NOT_422: GET /bots/status returns 200 — no route collision with /bots/{meeting_id}; compose: smoke-contract/BOTS_STATUS_NOT_422: GET /bots/status returns 200 — no r… |
| removal | Container fully removed after DELETE /bots/... | 10 | ✅ pass | helm: containers/removal: container fully removed after stop |
| status-completed | Meeting.status=completed after stop (not failed/stuck) | 10 | ✅ pass | helm: containers/status_completed: meeting.status=completed after stop (waited 1x5s) |
| graceful-leave | Bot leaves the meeting gracefully on stop (no force-kill by default) | 5 | ✅ pass | lite: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping treated as completed, not failed; compose: smoke-static/GRACEFUL_LEAVE: self_initiated_leave during stopping treated as compl… |
| route-collision | No Starlette route collisions — /bots/{id} and /bots/{platform}/{native_id} do not clash | 5 | ✅ pass | lite: smoke-static/ROUTE_COLLISION: bot detail route is /bots/id/{id}, not /bots/{id} which collides with /bots/status; compose: smoke-static/ROUTE_COLLISION: bot detail route is /bots/id/{id}, not… |
| timeout-stop | Bot auto-stops after automatic_leave timeout (no_one_joined_timeout) | 10 | ⚠️ skip | helm: containers/timeout_stop: bot still running after 60s (timeout may count from lobby) |
| concurrency-slot | Concurrent-bot slot released immediately on stop — next create succeeds | 10 | ✅ pass | helm: containers/concurrency_slot: slot released, B created (HTTP 201) |
| no-orphans | No zombie/exited bot containers left after a lifecycle run | 10 | ✅ pass | helm: containers/no_orphans: no exited/zombie containers |
| status-webhooks-fire | Status-change webhooks fire for every transition when enabled in webhook_events | 5 | ✅ pass | helm: webhooks/e2e_status: 1 status-change webhook(s) fired: meeting.status_change |

### `browser-session` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `container-lifecycle` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `dashboard` (95% / gate 90%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| login-flow | POST /api/auth/send-magic-link → 200 + success=true + sets vexa-token cookie | 10 | ✅ pass | lite: dashboard-auth/login: 200 + success=true; compose: dashboard-auth/login: 200 + success=true; helm: dashboard-auth/login: 200 + success=true |
| cookie-flags | vexa-token cookie Secure flag matches deployment (Secure iff https) | 10 | ✅ pass | lite: dashboard-auth/cookie_flags: flags correct for http; compose: dashboard-auth/cookie_flags: flags correct for http; helm: dashboard-auth/cookie_flags: flags correct for http |
| identity-me | GET /api/auth/me returns logged-in user's email (never falls back to env) | 10 | ✅ pass | lite: dashboard-auth/identity: /me returns test@vexa.ai; compose: dashboard-auth/identity: /me returns test@vexa.ai; helm: dashboard-auth/identity: /me returns test@vexa.ai |
| cookie-security | HttpOnly + SameSite cookies on magic-link send/verify + admin-verify + nextauth | 10 | ✅ pass | lite: smoke-static/SECURE_COOKIE_SEND_MAGIC_LINK: cookie Secure flag based on actual protocol, not NODE_ENV (send-magic-link); compose: smoke-static/SECURE_COOKIE_SEND_MAGIC_LINK: cookie Secure fla… |
| login-redirect | Magic-link click redirects to /meetings (not disabled /agent) | 5 | ✅ pass | lite: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to disabled /agent page; compose: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to disabled /… |
| identity-no-fallback | /api/auth/me uses only the cookie for identity, never env fallback | 5 | ✅ pass | lite: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses only cookie for identity, never falls back to env var; compose: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses only cookie for ident… |
| proxy-reachable | GET /api/vexa/meetings via cookie returns 200 | 10 | ✅ pass | lite: dashboard-auth/proxy_reachable: /api/vexa/meetings → 200; compose: dashboard-auth/proxy_reachable: /api/vexa/meetings → 200; helm: dashboard-auth/proxy_reachable: /api/vexa/meetings → 200 |
| meetings-list | /api/vexa/meetings returns a meeting list through the dashboard proxy | 5 | ✅ pass | compose: dashboard-proxy/meetings_list: 4 meetings; helm: dashboard-proxy/meetings_list: 11 meetings |
| pagination | limit/offset pagination works (no overlap between pages) | 5 | ✅ pass | compose: dashboard-proxy/pagination: limit/offset works, no overlap; helm: dashboard-proxy/pagination: limit/offset works, no overlap |
| field-contract | Meeting records include native_meeting_id / platform_specific_id | 5 | ✅ pass | compose: dashboard-proxy/field_contract: native_meeting_id present; helm: dashboard-proxy/field_contract: native_meeting_id present |
| transcript-proxy | Transcript reachable through dashboard proxy | 5 | ⚠️ skip | compose: dashboard-proxy/transcript_proxy: no meetings with transcripts; helm: dashboard-proxy/transcript_proxy: no meetings with transcripts |
| bot-create-proxy | POST /api/vexa/bots reaches the gateway and creates a bot (or returns 403/409) | 5 | ✅ pass | compose: dashboard-proxy/bot_create_proxy: HTTP 201; helm: dashboard-proxy/bot_create_proxy: HTTP 201 |
| dashboard-up | Dashboard root page responds | 5 | ✅ pass | lite: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; compose: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; helm: smoke-health/DASHBOARD_U… |
| dashboard-ws-url | NEXT_PUBLIC_WS_URL is set — live updates can connect | 5 | ✅ pass | lite: smoke-health/DASHBOARD_WS_URL: ws://localhost:3000/ws; compose: smoke-health/DASHBOARD_WS_URL: ws://localhost:3001/ws; helm: smoke-health/DASHBOARD_WS_URL: ws://172.238.170.161:30001/ws |
| dashboard-admin-key-valid | Dashboard's VEXA_ADMIN_API_KEY is accepted by admin-api (login path works) | 5 | ✅ pass | lite: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard can authenticate to admin-api — user lookup and login will work; compose: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard can authenticate to adm… |

### `infrastructure` (100% / gate 100%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| gateway-up | API gateway responds to /admin/users via valid admin token | 10 | ✅ pass | lite: smoke-health/GATEWAY_UP: API gateway accepts connections — all client requests can reach backend; compose: smoke-health/GATEWAY_UP: API gateway accepts connections — all client requests can r… |
| admin-api-up | admin-api responds with a valid list | 10 | ✅ pass | lite: smoke-health/ADMIN_API_UP: admin-api responds with valid token — user management and login work; compose: smoke-health/ADMIN_API_UP: admin-api responds with valid token — user management and … |
| dashboard-up | dashboard root page responds | 10 | ✅ pass | lite: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; compose: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; helm: smoke-health/DASHBOARD_U… |
| runtime-api-up | runtime-api (bot orchestrator) is reachable / has ready replicas | 15 | ✅ pass | lite: smoke-health/RUNTIME_API_UP: runtime-api responds — bot container lifecycle management works; compose: smoke-health/RUNTIME_API_UP: runtime-api responds — bot container lifecycle management w… |
| transcription-up | transcription service /health returns ok + gpu_available | 15 | ✅ pass | lite: smoke-health/TRANSCRIPTION_UP: transcription service responds — audio can be converted to text; compose: smoke-health/TRANSCRIPTION_UP: transcription service responds — audio can be converted… |
| redis-up | Redis responds to PING | 10 | ✅ pass | lite: smoke-health/REDIS_UP: Redis responds to PING — WebSocket pub/sub, session state, and caching work; compose: smoke-health/REDIS_UP: Redis responds to PING — WebSocket pub/sub, session state, … |
| minio-up | MinIO is healthy / has ready replicas | 10 | ✅ pass | compose: smoke-health/MINIO_UP: MinIO responds — recordings and browser state storage work; helm: smoke-health/MINIO_UP: 1 ready replicas |
| db-schema | Database schema is aligned with the current model | 10 | ✅ pass | lite: smoke-health/DB_SCHEMA_ALIGNED: all required columns present; compose: smoke-health/DB_SCHEMA_ALIGNED: all required columns present; helm: smoke-health/DB_SCHEMA_ALIGNED: all required columns… |
| gateway-timeout | Gateway proxy timeout is ≥30s (prevents premature 504s under load) | 10 | ✅ pass | lite: smoke-static/GATEWAY_TIMEOUT_ADEQUATE: API gateway HTTP client timeout >= 15s — browser session creation needs time; compose: smoke-static/GATEWAY_TIMEOUT_ADEQUATE: API gateway HTTP client ti… |
| chart-resources-tuned | every enabled service in values.yaml declares resources.requests + resources.limits for both cpu and memory | 10 | ✅ pass | helm: smoke-static/HELM_VALUES_RESOURCES_SET: values.yaml declares explicit resources.requests.cpu on service blocks — no service ships without a CPU request |
| chart-security-hardened | global.securityContext sets allowPrivilegeEscalation: false and drops ALL capabilities | 10 | ✅ pass | helm: smoke-static/HELM_GLOBAL_SECURITY_HARDENED: global.securityContext blocks privilege escalation and drops all Linux capabilities — pods run with minimum required privileges |
| chart-redis-tuned | redis deployment args include --maxmemory and an eviction policy | 10 | ✅ pass | helm: smoke-static/HELM_REDIS_MAXMEMORY_SET: redis deployment is capped at a specific maxmemory with an eviction policy — no unbounded growth |
| chart-db-pool-tuned | meeting-api env sets DB_POOL_SIZE (pool sizing is an explicit, reviewed choice) | 10 | ✅ pass | helm: smoke-static/HELM_MEETING_API_DB_POOL_TUNED: meeting-api values set an explicit DB_POOL_SIZE — pool sizing is a conscious choice, not an asyncpg default |
| chart-pdb-available | PodDisruptionBudget template exists in chart (off by default via values toggle; on when podDisruptionBudgets.<svc>.enabled=true) | 10 | ✅ pass | helm: smoke-static/HELM_PDB_TEMPLATE_EXISTS: the chart carries a PodDisruptionBudget template (enablement is a values toggle) — availability contracts are first-class |

### `meeting-chat` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `meeting-urls` (100% / gate 100%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| url-parser-exists | meeting-api has a URL parser module (url_parser.py) that handles platform detection | 10 | ✅ pass | lite: smoke-static/URL_PARSER_EXISTS: MeetingCreate schema has parse_meeting_url — accepts meeting_url field directly; compose: smoke-static/URL_PARSER_EXISTS: MeetingCreate schema has parse_meetin… |
| gmeet-parsed | Google Meet URL (meet.google.com/xxx-xxxx-xxx) parses correctly | 15 | ✅ pass | lite: smoke-contract/GMEET_URL_PARSED: Google Meet URL accepted by POST /bots — parser handles GMeet format; compose: smoke-contract/GMEET_URL_PARSED: Google Meet URL accepted by POST /bots — parse… |
| invalid-rejected | Invalid meeting URL returns 400 (not 500) | 10 | ✅ pass | lite: smoke-contract/INVALID_URL_REJECTED: garbage URLs rejected with 400/422 — input validation works; compose: smoke-contract/INVALID_URL_REJECTED: garbage URLs rejected with 400/422 — input vali… |
| teams-standard | Teams standard link (teams.microsoft.com/l/meetup-join/...) parses | 15 | ✅ pass | lite: smoke-contract/TEAMS_URL_STANDARD: Teams standard join URL accepted by POST /bots; compose: smoke-contract/TEAMS_URL_STANDARD: Teams standard join URL accepted by POST /bots; helm: smoke-cont… |
| teams-shortlink | Teams shortlink (teams.live.com, teams.microsoft.com/meet) parses | 10 | ✅ pass | lite: smoke-contract/TEAMS_URL_SHORTLINK: Teams /meet/ shortlink URL parsed and accepted by POST /bots (no explicit platform needed); compose: smoke-contract/TEAMS_URL_SHORTLINK: Teams /meet/ short… |
| teams-channel | Teams channel meeting URL parses | 10 | ✅ pass | lite: smoke-contract/TEAMS_URL_CHANNEL: Teams channel meeting URL accepted or known gap; compose: smoke-contract/TEAMS_URL_CHANNEL: Teams channel meeting URL accepted or known gap; helm: smoke-cont… |
| teams-enterprise | Teams enterprise-tenant URL parses (custom domain) | 15 | ✅ pass | lite: smoke-contract/TEAMS_URL_ENTERPRISE: Teams enterprise domain URL parsed and accepted by POST /bots (no explicit platform needed); compose: smoke-contract/TEAMS_URL_ENTERPRISE: Teams enterpris… |
| teams-personal | Teams personal-account URL parses | 15 | ✅ pass | lite: smoke-contract/TEAMS_URL_PERSONAL: Teams personal (teams.live.com) URL parsed and accepted by POST /bots (no explicit platform needed); compose: smoke-contract/TEAMS_URL_PERSONAL: Teams perso… |

### `post-meeting-transcription` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `realtime-transcription` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `realtime-transcription/gmeet` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `realtime-transcription/msteams` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `realtime-transcription/zoom` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `remote-browser` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `speaking-bot` (0% / gate 0%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|

### `webhooks` (100% / gate 95%)

| # | Label | Weight | Status | Evidence |
|---|-------|-------:|:------:|----------|
| events-meeting-completed | meeting.completed fires on every bot exit (default-enabled) | 10 | ✅ pass | compose: webhooks/e2e_completion: webhook_delivery.status=delivered |
| events-status-webhooks | Status-change webhooks for non-meeting.completed events (meeting.started / meeting.status_change / bot.failed) fire when opted-in via webhook_events — proven by a delivery with event_type != meeting.completed, not by any entry in webhook_deliveries[]. | 10 | ✅ pass | helm: webhooks/e2e_status_non_completed: non-meeting.completed status event(s) fired: meeting.status_change |
| envelope-shape | Every webhook carries envelope: event_id, event_type, api_version, created_at, data | 10 | ✅ pass | compose: webhooks/envelope: event_id, event_type, api_version, created_at, data present |
| headers-hmac | X-Webhook-Signature = HMAC-SHA256(timestamp + '.' + payload) when secret is set | 10 | ✅ pass | compose: webhooks/hmac: HMAC-SHA256 64-char digest |
| security-spoof-protection | Client-supplied X-User-Webhook-* headers cannot override stored config | 10 | ✅ pass | compose: webhooks/spoof: client header stripped (stored webhook_url=https://httpbin.org/post) |
| security-secret-not-exposed | webhook_secret never appears in any API response (POST /bots, GET /bots/status) | 10 | ✅ pass | compose: webhooks/no_leak_response: webhook_secret not in /bots/status response |
| security-payload-hygiene | Internal fields (secret, url, container ids, delivery state) stripped from webhook payloads | 5 | ✅ pass | compose: webhooks/no_leak_payload: internal fields stripped; user fields preserved |
| flow-user-config | PUT /user/webhook persists webhook_url + webhook_secret + webhook_events to User.data | 10 | ✅ pass | compose: webhooks/config: user webhook set via PUT /user/webhook |
| flow-gateway-inject | Gateway injects validated webhook config into meeting.data on POST /bots | 15 | ✅ pass | compose: webhooks/inject: gateway injected webhook_url=https://httpbin.org/post (after cache expiry) |
| reliability-db-pool | DB connection pool doesn't exhaust under repeated status requests | 10 | ✅ pass | lite: smoke-contract/DB_POOL_NO_EXHAUSTION: 10/10 requests returned 200; compose: smoke-contract/DB_POOL_NO_EXHAUSTION: 10/10 requests returned 200 |

## Raw test results

### `compose`

| Test | Status | Duration | Steps (pass / total) |
|------|:------:|---------:|---------------------:|
| `containers` | ✅ pass | 108835 ms | 6 / 7 |
| `dashboard-auth` | ✅ pass | 464 ms | 4 / 4 |
| `dashboard-proxy` | ✅ pass | 1025 ms | 5 / 6 |
| `smoke-contract` | ✅ pass | 91148 ms | 25 / 25 |
| `smoke-env` | ❌ fail | 600 ms | 6 / 7 |
| `smoke-health` | ✅ pass | 5078 ms | 13 / 17 |
| `smoke-static` | ✅ pass | 5 ms | 23 / 24 |
| `webhooks` | ✅ pass | 96535 ms | 9 / 9 |

### `helm`

| Test | Status | Duration | Steps (pass / total) |
|------|:------:|---------:|---------------------:|
| `containers` | ✅ pass | 114607 ms | 6 / 7 |
| `dashboard-auth` | ✅ pass | 1465 ms | 4 / 4 |
| `dashboard-proxy` | ✅ pass | 5146 ms | 4 / 6 |
| `smoke-contract` | ✅ pass | 20957 ms | 24 / 25 |
| `smoke-env` | ✅ pass | 9632 ms | 7 / 7 |
| `smoke-health` | ✅ pass | 35627 ms | 17 / 17 |
| `smoke-static` | ✅ pass | 213 ms | 28 / 29 |
| `webhooks` | ✅ pass | 41777 ms | 9 / 10 |

### `lite`

| Test | Status | Duration | Steps (pass / total) |
|------|:------:|---------:|---------------------:|
| `containers` | ❌ fail | 25418 ms | 2 / 2 |
| `dashboard-auth` | ✅ pass | 471 ms | 4 / 4 |
| `smoke-contract` | ✅ pass | 39109 ms | 21 / 25 |
| `smoke-env` | ✅ pass | 720 ms | 7 / 7 |
| `smoke-health` | ✅ pass | 5076 ms | 12 / 17 |
| `smoke-static` | ✅ pass | 5 ms | 23 / 24 |
| `webhooks` | ✅ pass | 96425 ms | 10 / 10 |
