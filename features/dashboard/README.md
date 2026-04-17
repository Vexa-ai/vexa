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
<!-- Auto-written by tests3/lib/aggregate.py from release tag `0.10.0-260417-1408`. Do not edit by hand — edit the `tests3.dods:` frontmatter + re-run `make -C tests3 report --write-features`. -->

**Confidence: 0%** (gate: 90%, status: ❌ below gate)

| # | Behavior | Weight | Status | Evidence (modes) |
|---|----------|-------:|:------:|------------------|
| login-flow | POST /api/auth/send-magic-link → 200 + success=true + sets vexa-token cookie | 10 | ⬜ missing | `lite`: no report for test=dashboard-auth; `compose`: no report for test=dashboard-auth; `helm`: no report for test=dashboard-auth |
| cookie-flags | vexa-token cookie Secure flag matches deployment (Secure iff https) | 10 | ⬜ missing | `lite`: no report for test=dashboard-auth; `compose`: no report for test=dashboard-auth; `helm`: no report for test=dashboard-auth |
| identity-me | GET /api/auth/me returns logged-in user's email (never falls back to env) | 10 | ⬜ missing | `lite`: no report for test=dashboard-auth; `compose`: no report for test=dashboard-auth; `helm`: no report for test=dashboard-auth |
| cookie-security | HttpOnly + SameSite cookies on magic-link send/verify + admin-verify + nextauth | 10 | ⬜ missing | `lite`: check SECURE_COOKIE_SEND_MAGIC_LINK not found in any smoke-* report; `compose`: check SECURE_COOKIE_SEND_MAGIC_LINK not found in any smoke-* report; `helm`: smoke-static/SECURE_COOKIE_SEND_MAGIC_LINK: cookie Secure flag based on actual protocol, not NODE_ENV (send-magic-link) |
| login-redirect | Magic-link click redirects to /meetings (not disabled /agent) | 5 | ⬜ missing | `lite`: check LOGIN_REDIRECT not found in any smoke-* report; `compose`: check LOGIN_REDIRECT not found in any smoke-* report; `helm`: smoke-static/LOGIN_REDIRECT: login redirects to / (then /meetings), not to disabled /agent page |
| identity-no-fallback | /api/auth/me uses only the cookie for identity, never env fallback | 5 | ⬜ missing | `lite`: check IDENTITY_NO_FALLBACK not found in any smoke-* report; `compose`: check IDENTITY_NO_FALLBACK not found in any smoke-* report; `helm`: smoke-static/IDENTITY_NO_FALLBACK: /api/auth/me uses only cookie for identity, never falls back to env var |
| proxy-reachable | GET /api/vexa/meetings via cookie returns 200 | 10 | ⬜ missing | `lite`: no report for test=dashboard-auth; `compose`: no report for test=dashboard-auth; `helm`: no report for test=dashboard-auth |
| meetings-list | /api/vexa/meetings returns a meeting list through the dashboard proxy | 5 | ⬜ missing | `compose`: no report for test=dashboard-proxy; `helm`: no report for test=dashboard-proxy |
| pagination | limit/offset pagination works (no overlap between pages) | 5 | ⬜ missing | `compose`: no report for test=dashboard-proxy; `helm`: no report for test=dashboard-proxy |
| field-contract | Meeting records include native_meeting_id / platform_specific_id | 5 | ⬜ missing | `compose`: no report for test=dashboard-proxy; `helm`: no report for test=dashboard-proxy |
| transcript-proxy | Transcript reachable through dashboard proxy | 5 | ⬜ missing | `compose`: no report for test=dashboard-proxy; `helm`: no report for test=dashboard-proxy |
| bot-create-proxy | POST /api/vexa/bots reaches the gateway and creates a bot (or returns 403/409) | 5 | ⬜ missing | `compose`: no report for test=dashboard-proxy; `helm`: no report for test=dashboard-proxy |
| dashboard-up | Dashboard root page responds | 5 | ⬜ missing | `lite`: check DASHBOARD_UP not found in any smoke-* report; `compose`: check DASHBOARD_UP not found in any smoke-* report; `helm`: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI |
| dashboard-ws-url | NEXT_PUBLIC_WS_URL is set — live updates can connect | 5 | ⬜ missing | `lite`: check DASHBOARD_WS_URL not found in any smoke-* report; `compose`: check DASHBOARD_WS_URL not found in any smoke-* report; `helm`: smoke-health/DASHBOARD_WS_URL: wss://dashboard.staging.vexa.ai/ws |
| dashboard-admin-key-valid | Dashboard's VEXA_ADMIN_API_KEY is accepted by admin-api (login path works) | 5 | ⬜ missing | `lite`: check DASHBOARD_ADMIN_KEY_VALID not found in any smoke-* report; `compose`: check DASHBOARD_ADMIN_KEY_VALID not found in any smoke-* report; `helm`: smoke-env/DASHBOARD_ADMIN_KEY_VALID: dashboard can authenticate to admin-api — user lookup and login will work |

<!-- END AUTO-DOD -->

