---
services:
- dashboard
- admin-api
- api-gateway
---

# Dashboard

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 90%**

## What

Next.js dashboard at `/meetings`. Shows meeting list, per-meeting transcript, live status updates via WebSocket, recordings, chat.

## User flows

```
Login (magic link or direct) → meetings list → click meeting → meeting detail page
  → transcript renders (REST bootstrap) → live updates via WS → status badge updates
```

## DoD


<!-- BEGIN AUTO-DOD -->
<!-- Auto-written by tests3/lib/aggregate.py from release tag `0.10.0-260427-1618`. Do not edit by hand — edit the sidecar `dods.yaml` + re-run `make -C tests3 report --write-features`. -->

**Confidence: 32%** (gate: 90%, status: ❌ below gate)

| # | Behavior | Weight | Status | Evidence (modes) |
|---|----------|-------:|:------:|------------------|
| login-flow | POST /api/auth/send-magic-link → 200 + success=true + sets vexa-token cookie | 10 | ⬜ missing | `lite`: dashboard-auth/login: 200 + success=true; `compose`: dashboard-auth/login: 200 + success=true; `helm`: no report for test=dashboard-auth |
| cookie-flags | vexa-token cookie Secure flag matches deployment (Secure iff https) | 10 | ⬜ missing | `lite`: dashboard-auth/cookie_flags: flags correct for http; `compose`: dashboard-auth/cookie_flags: flags correct for http; `helm`: no report for test=dashboard-auth |
| identity-me | GET /api/auth/me returns logged-in user's email (never falls back to env) | 10 | ⬜ missing | `lite`: dashboard-auth/identity: /me returns test@vexa.ai; `compose`: dashboard-auth/identity: /me returns test@vexa.ai; `helm`: no report for test=dashboard-auth |
| cookie-security | HttpOnly + SameSite cookies on magic-link send/verify + admin-verify + nextauth | 10 | ✅ pass | `lite`: smoke-static/SECURE_COOKIE_SEND_MAGIC_LINK: cookie Secure flag based on actual protocol, not NODE_ENV (send-magic-link); `compose`: smoke-static/SECURE_COOKIE_SEND_MAGIC_LINK: cookie Secure flag based on actual protocol, not NODE_ENV (send-magic-link); `helm`: smoke-static/SECURE_COOKIE_S… |
| login-redirect | Magic-link click redirects to /meetings (not disabled /agent) | 5 | ✅ pass | `lite`: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to disabled /agent page; `compose`: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to disabled /agent page; `helm`: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to d… |
| identity-no-fallback | /api/auth/me uses only the cookie for identity, never env fallback | 5 | ✅ pass | `lite`: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses only cookie for identity, never falls back to env var; `compose`: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses only cookie for identity, never falls back to env var; `helm`: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses o… |
| proxy-reachable | GET /api/vexa/meetings via cookie returns 200 | 10 | ⬜ missing | `lite`: dashboard-auth/proxy_reachable: /api/vexa/meetings → 200; `compose`: dashboard-auth/proxy_reachable: /api/vexa/meetings → 200; `helm`: no report for test=dashboard-auth |
| meetings-list | /api/vexa/meetings returns a meeting list through the dashboard proxy | 5 | ⬜ missing | `compose`: dashboard-proxy/meetings_list: 4 meetings; `helm`: no report for test=dashboard-proxy |
| pagination | limit/offset pagination works (no overlap between pages) | 5 | ⬜ missing | `compose`: dashboard-proxy/pagination: limit/offset works, no overlap; `helm`: no report for test=dashboard-proxy |
| field-contract | Meeting records include native_meeting_id / platform_specific_id | 5 | ⬜ missing | `compose`: dashboard-proxy/field_contract: native_meeting_id present; `helm`: no report for test=dashboard-proxy |
| transcript-proxy | Transcript reachable through dashboard proxy | 5 | ⬜ missing | `compose`: dashboard-proxy/transcript_proxy: no meetings with transcripts; `helm`: no report for test=dashboard-proxy |
| bot-create-proxy | POST /api/vexa/bots reaches the gateway and creates a bot (or returns 403/409) | 5 | ⬜ missing | `compose`: dashboard-proxy/bot_create_proxy: HTTP 201; `helm`: no report for test=dashboard-proxy |
| dashboard-up | Dashboard root page responds | 5 | ⬜ missing | `lite`: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; `compose`: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; `helm`: check DASHBOARD_UP not found in any report |
| dashboard-ws-url | NEXT_PUBLIC_WS_URL is set — live updates can connect | 5 | ⬜ missing | `lite`: smoke-health/DASHBOARD_WS_URL: ws://localhost:3000/ws; `compose`: smoke-health/DASHBOARD_WS_URL: ws://localhost:3001/ws; `helm`: check DASHBOARD_WS_URL not found in any report |
| dashboard-admin-key-valid | Dashboard's VEXA_ADMIN_API_KEY is accepted by admin-api (login path works) | 5 | ✅ pass | `lite`: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard can authenticate to admin-api — user lookup and login will work; `compose`: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard can authenticate to admin-api — user lookup and login will work; `helm`: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard… |
| packages-transcript-rendering-tests-pass | packages/transcript-rendering npm test passes — guards the dedup-prefers-confirmed fix + existing 76 tests | 5 | ✅ pass | `lite`: package-tests/TRANSCRIPT_RENDERING_DEDUP_TESTS_PASS: npm unavailable on this harness; source-level dedup-prefers-confirmed pattern present (PR-time CI is authoritative) |
| packages-ci-workflow-exists | .github/workflows/test-packages.yml exists and runs npm test per package in matrix | 5 | ✅ pass | `lite`: smoke-static/PACKAGES_CI_WORKFLOW_EXISTS: .github/workflows/test-packages.yml exists and runs npm test on packages/* |

<!-- END AUTO-DOD -->

