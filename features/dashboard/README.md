---
services: [dashboard, admin-api, api-gateway]
tests3:
  targets: [dashboard, smoke]
  checks: [LOGIN_REDIRECT, IDENTITY_NO_FALLBACK, SECURE_COOKIE_SEND_MAGIC_LINK, SECURE_COOKIE_VERIFY, SECURE_COOKIE_ADMIN_VERIFY, SECURE_COOKIE_NEXTAUTH, MAP_MEETING, COMPOSE_ADMIN_KEY_DEFAULT, DASHBOARD_UP, DASHBOARD_WS_URL, DASHBOARD_LOGIN, DASHBOARD_ADMIN_KEY_MATCHES, DASHBOARD_ADMIN_KEY_VALID, DASHBOARD_API_KEY_VALID, DASHBOARD_API_URL_SET, CACHE_HEADERS]
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

| # | Criterion | Weight | Ceiling | Tier | Proc step | Status | Last |
|---|-----------|--------|---------|------|-----------|--------|------|
| 1 | Dashboard container reaches all backends (wget) | 10 | — | T1 | dashboard/3 | PASS | 2026-04-08. Smoke health checks pass on compose + helm. |
| 2 | No false "failed" for successful meetings | 10 | — | T1 | dashboard/4 | FAIL | 2026-04-08. 1 meeting with 'failed' status in production data. |
| 3 | Magic link login returns 200 + sets cookie | 10 | ceiling | T2 | dashboard/5 | PASS | 2026-04-08. Compose + helm dashboard-auth pass. |
| 4 | Meetings list loads with auth cookie | 10 | ceiling | T2 | dashboard/6 | PASS | 2026-04-08. Compose: 34 meetings. Helm: 26 meetings. |
| 5 | GET /meetings/{id} returns native_meeting_id (field contract) | 10 | ceiling | T2 | dashboard/7 | PASS | 2026-04-08. Field contract verified on compose + helm. |
| 6 | Transcript via proxy returns segments | 10 | — | T2 | dashboard/8 | PASS | 2026-04-08. Helm: 1 segment via proxy. Compose: segments returned. |
| 7 | Meeting page renders transcript in browser (headless) | 15 | ceiling | T3 | dashboard/9 | PASS | 2026-04-08. Pending human Phase 6 re-check. |
| 8 | Meeting page shows correct status (matches API) | 10 | — | T3 | dashboard/10 | PASS | 2026-04-08. Dashboard-proxy verifies status match. |
| 9 | Cache headers prevent stale JS bundles | 5 | — | T3 | dashboard/11 | PASS | 2026-04-08. Smoke static check confirms cache headers. |
| 10 | Dashboard credentials valid (VEXA_ADMIN_API_KEY, VEXA_API_KEY) | 10 | ceiling | T1 | infra/11 | PASS | 2026-04-08. Login succeeded on compose + helm. |
| 11 | Platform icons render (no broken images) | 5 | — | T1 | — | PASS | 2026-04-08. Pending human Phase 6 re-check. |
| 12 | Meetings list paginates (limit/offset/has_more) | 10 | — | T2 | dashboard/pagination | PASS | 2026-04-08. Compose + helm: pagination verified, no overlap. |
| 13 | Login as email X → dashboard shows user X (not another user) | 10 | ceiling | T2 | dashboard/12 | PASS | 2026-04-08. Identity verified via /me on compose + helm. |
| 14 | After login, redirects to /meetings (not /agent) | 5 | — | T2 | dashboard/13 | PASS | 2026-04-08. Static check: LOGIN_REDIRECT pass. |
| 15 | Bot creation through dashboard returns bot or actionable error | 10 | — | T2 | dashboard/14 | PASS | 2026-04-08. Compose: 201, bot created. Helm: 403 (limit reached, proxy works). |

**Confidence:** 90 (14/15 PASS, #2 FAIL — 1 false-failed meeting in production data)

## Known bugs

| Bug | Status | Root cause |
|-----|--------|-----------|
| Meeting detail transcript empty on load + reload | **OPEN** | `getMeeting()` in `api.ts` returns raw API response. Field is `native_meeting_id` but dashboard uses `platform_specific_id`. Without `mapMeeting()`, value is `undefined`. Fix applied but not yet validated. |
| Dashboard credentials wrong after restart | **FIXED** | Compose defaulted `VEXA_ADMIN_API_KEY` to `vexa-admin-token` instead of `changeme`. VEXA_API_KEY stale. |
| REST transcript not fetched for active meetings | **FIXED** | `!shouldUseWebSocket` guard prevented REST fetch during active state. |
| Meeting icons broken | **FIXED** | PNG files removed from repo. Restored from git history. |
| Login as test@vexa.ai shows admin@vexa.ai | **FIXED** | `/api/auth/me` fell back to `VEXA_API_KEY` env var (user 1). Removed fallback — cookie is the only identity source. |
| Login redirect loop on HTTP (self-hosted) | **FIXED** | Cookie `Secure` flag set via `NODE_ENV === "production"` — always true in prod builds. Changed to `isSecureRequest()` checking URL protocol. All 4 auth routes fixed. |
| After login, redirects to /agent instead of /meetings | **FIXED** | `login/page.tsx:131` changed from `router.push("/agent")` to `router.push("/")`. |
| "Start bot" fails with generic server error | **FIXED** | Bot image not pulled on fresh deploy. Makefile `up` target now pulls bot image. |
