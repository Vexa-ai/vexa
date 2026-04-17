---
services: [dashboard, admin-api, api-gateway]
tests3:
  gate:
    confidence_min: 90
  dods:
    # ── Auth flow ─────────────────────────────────────────────
    - id: login-flow
      label: "POST /api/auth/send-magic-link → 200 + success=true + sets vexa-token cookie"
      weight: 10
      evidence: {test: dashboard-auth, step: login, modes: [lite, compose, helm]}
    - id: cookie-flags
      label: "vexa-token cookie Secure flag matches deployment (Secure iff https)"
      weight: 10
      evidence: {test: dashboard-auth, step: cookie_flags, modes: [lite, compose, helm]}
    - id: identity-me
      label: "GET /api/auth/me returns logged-in user's email (never falls back to env)"
      weight: 10
      evidence: {test: dashboard-auth, step: identity, modes: [lite, compose, helm]}
    - id: cookie-security
      label: "HttpOnly + SameSite cookies on magic-link send/verify + admin-verify + nextauth"
      weight: 10
      evidence: {check: SECURE_COOKIE_SEND_MAGIC_LINK, modes: [lite, compose, helm]}
    - id: login-redirect
      label: "Magic-link click redirects to /meetings (not disabled /agent)"
      weight: 5
      evidence: {check: LOGIN_REDIRECT, modes: [lite, compose, helm]}
    - id: identity-no-fallback
      label: "/api/auth/me uses only the cookie for identity, never env fallback"
      weight: 5
      evidence: {check: IDENTITY_NO_FALLBACK, modes: [lite, compose, helm]}

    # ── Proxy flow ────────────────────────────────────────────
    - id: proxy-reachable
      label: "GET /api/vexa/meetings via cookie returns 200"
      weight: 10
      evidence: {test: dashboard-auth, step: proxy_reachable, modes: [lite, compose, helm]}
    - id: meetings-list
      label: "/api/vexa/meetings returns a meeting list through the dashboard proxy"
      weight: 5
      evidence: {test: dashboard-proxy, step: meetings_list, modes: [compose, helm]}
    - id: pagination
      label: "limit/offset pagination works (no overlap between pages)"
      weight: 5
      evidence: {test: dashboard-proxy, step: pagination, modes: [compose, helm]}
    - id: field-contract
      label: "Meeting records include native_meeting_id / platform_specific_id"
      weight: 5
      evidence: {test: dashboard-proxy, step: field_contract, modes: [compose, helm]}
    - id: transcript-proxy
      label: "Transcript reachable through dashboard proxy"
      weight: 5
      evidence: {test: dashboard-proxy, step: transcript_proxy, modes: [compose, helm]}
    - id: bot-create-proxy
      label: "POST /api/vexa/bots reaches the gateway and creates a bot (or returns 403/409)"
      weight: 5
      evidence: {test: dashboard-proxy, step: bot_create_proxy, modes: [compose, helm]}

    # ── Config / health ───────────────────────────────────────
    - id: dashboard-up
      label: "Dashboard root page responds"
      weight: 5
      evidence: {check: DASHBOARD_UP, modes: [lite, compose, helm]}
    - id: dashboard-ws-url
      label: "NEXT_PUBLIC_WS_URL is set — live updates can connect"
      weight: 5
      evidence: {check: DASHBOARD_WS_URL, modes: [lite, compose, helm]}
    - id: dashboard-admin-key-valid
      label: "Dashboard's VEXA_ADMIN_API_KEY is accepted by admin-api (login path works)"
      weight: 5
      evidence: {check: DASHBOARD_ADMIN_KEY_VALID, modes: [lite, compose, helm]}
---

# Dashboard

## What

Next.js dashboard at `/meetings`. Shows meeting list, per-meeting transcript, live status updates via WebSocket, recordings, chat.

## User flows

```
Login (magic link or direct) → meetings list → click meeting → meeting detail page
  → transcript renders (REST bootstrap) → live updates via WS → status badge updates
```

## DoD


<!-- BEGIN AUTO-DOD -->
<!-- Auto-written by tests3/lib/aggregate.py from release tag `0.10.0-260417-1454`. Do not edit by hand — edit the `tests3.dods:` frontmatter + re-run `make -C tests3 report --write-features`. -->

**Confidence: 90%** (gate: 90%, status: ✅ pass)

| # | Behavior | Weight | Status | Evidence (modes) |
|---|----------|-------:|:------:|------------------|
| login-flow | POST /api/auth/send-magic-link → 200 + success=true + sets vexa-token cookie | 10 | ✅ pass | `lite`: dashboard-auth/login: 200 + success=true; `compose`: dashboard-auth/login: 200 + success=true; `helm`: dashboard-auth/login: 200 + success=true |
| cookie-flags | vexa-token cookie Secure flag matches deployment (Secure iff https) | 10 | ✅ pass | `lite`: dashboard-auth/cookie_flags: flags correct for http; `compose`: dashboard-auth/cookie_flags: flags correct for http; `helm`: dashboard-auth/cookie_flags: flags correct for http |
| identity-me | GET /api/auth/me returns logged-in user's email (never falls back to env) | 10 | ✅ pass | `lite`: dashboard-auth/identity: /me returns test@vexa.ai; `compose`: dashboard-auth/identity: /me returns test@vexa.ai; `helm`: dashboard-auth/identity: /me returns test@vexa.ai |
| cookie-security | HttpOnly + SameSite cookies on magic-link send/verify + admin-verify + nextauth | 10 | ✅ pass | `lite`: smoke-static/SECURE_COOKIE_SEND_MAGIC_LINK: cookie Secure flag based on actual protocol, not NODE_ENV (send-magic-link); `compose`: smoke-static/SECURE_COOKIE_SEND_MAGIC_LINK: cookie Secure flag based on actual protocol, not NODE_ENV (send-magic-link); `helm`: smoke-static/SECURE_COOKIE_S… |
| login-redirect | Magic-link click redirects to /meetings (not disabled /agent) | 5 | ✅ pass | `lite`: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to disabled /agent page; `compose`: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to disabled /agent page; `helm`: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to d… |
| identity-no-fallback | /api/auth/me uses only the cookie for identity, never env fallback | 5 | ✅ pass | `lite`: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses only cookie for identity, never falls back to env var; `compose`: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses only cookie for identity, never falls back to env var; `helm`: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses o… |
| proxy-reachable | GET /api/vexa/meetings via cookie returns 200 | 10 | ✅ pass | `lite`: dashboard-auth/proxy_reachable: /api/vexa/meetings → 200; `compose`: dashboard-auth/proxy_reachable: /api/vexa/meetings → 200; `helm`: dashboard-auth/proxy_reachable: /api/vexa/meetings → 200 |
| meetings-list | /api/vexa/meetings returns a meeting list through the dashboard proxy | 5 | ✅ pass | `compose`: dashboard-proxy/meetings_list: 4 meetings; `helm`: dashboard-proxy/meetings_list: 15 meetings |
| pagination | limit/offset pagination works (no overlap between pages) | 5 | ✅ pass | `compose`: dashboard-proxy/pagination: limit/offset works, no overlap; `helm`: dashboard-proxy/pagination: limit/offset works, no overlap |
| field-contract | Meeting records include native_meeting_id / platform_specific_id | 5 | ✅ pass | `compose`: dashboard-proxy/field_contract: native_meeting_id present; `helm`: dashboard-proxy/field_contract: native_meeting_id present |
| transcript-proxy | Transcript reachable through dashboard proxy | 5 | ⚠️ skip | `compose`: dashboard-proxy/transcript_proxy: no meetings with transcripts; `helm`: dashboard-proxy/transcript_proxy: no meetings with transcripts |
| bot-create-proxy | POST /api/vexa/bots reaches the gateway and creates a bot (or returns 403/409) | 5 | ❌ fail | `compose`: dashboard-proxy/bot_create_proxy: HTTP 201; `helm`: dashboard-proxy/bot_create_proxy: HTTP 500 — runtime-api or bot image broken |
| dashboard-up | Dashboard root page responds | 5 | ✅ pass | `lite`: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; `compose`: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; `helm`: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI |
| dashboard-ws-url | NEXT_PUBLIC_WS_URL is set — live updates can connect | 5 | ✅ pass | `lite`: smoke-health/DASHBOARD_WS_URL: ws://localhost:3000/ws; `compose`: smoke-health/DASHBOARD_WS_URL: ws://localhost:3001/ws; `helm`: smoke-health/DASHBOARD_WS_URL: ws://172.238.169.249:30001/ws |
| dashboard-admin-key-valid | Dashboard's VEXA_ADMIN_API_KEY is accepted by admin-api (login path works) | 5 | ✅ pass | `lite`: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard can authenticate to admin-api — user lookup and login will work; `compose`: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard can authenticate to admin-api — user lookup and login will work; `helm`: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard… |

<!-- END AUTO-DOD -->

