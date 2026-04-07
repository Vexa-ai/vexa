# Dashboard

> Proc: `tests2/src/dashboard.md`

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
| 1 | Dashboard container reaches all backends (wget) | 10 | — | T1 | dashboard/3 | PASS | 2026-04-07. docker exec: gateway=PASS, bots=PASS, admin=PASS, next-auth=PASS. |
| 2 | No false "failed" for successful meetings | 10 | — | T1 | dashboard/4 | FAIL | 2026-04-07. 3 false failures: meeting 9894 (139 segs, active→failed on page.evaluate crash), 9877 (15 segs), 9876 (15 segs). |
| 3 | Magic link login returns 200 + sets cookie | 10 | ceiling | T2 | dashboard/5 | PASS | 2026-04-07. POST /api/auth/send-magic-link 200, token returned, cookie set. |
| 4 | Meetings list loads with auth cookie | 10 | ceiling | T2 | dashboard/6 | PASS | 2026-04-07. GET /api/vexa/meetings with cookie: 50 meetings returned. |
| 5 | GET /meetings/{id} returns native_meeting_id (field contract) | 10 | ceiling | T2 | dashboard/7 | PASS | 2026-04-07. GET /api/vexa/meetings/9907 returns native_meeting_id="lifecycle-test-1". CONTRACT=PASS. |
| 6 | Transcript via proxy returns segments | 10 | — | T2 | dashboard/8 | PASS | 2026-04-07. Meeting 9893 (gbn-mvgb-for): 7 segments via /api/vexa/transcripts proxy. |
| 7 | Meeting page renders transcript in browser (headless) | 15 | ceiling | T3 | dashboard/9 | PASS | 2026-04-07. Meeting 9893: headless Playwright renders transcript — 86 words, timestamps (10:13:31-10:13:52), speaker text visible. |
| 8 | Meeting page shows correct status (matches API) | 10 | — | T3 | dashboard/10 | PASS | 2026-04-07. Meeting 9907: API status=completed, proxy status=completed. Match confirmed. |
| 9 | Cache headers prevent stale JS bundles | 5 | — | T3 | dashboard/11 | PASS | 2026-04-07. JS chunks: Cache-Control: public, max-age=31536000, immutable. Hashed filenames. |
| 10 | Dashboard credentials valid (VEXA_ADMIN_API_KEY, VEXA_API_KEY) | 10 | ceiling | T1 | infra/11 | PASS | 2026-04-07. Login succeeded, meetings list loaded — credentials valid. |
| 11 | Platform icons render (no broken images) | 5 | — | T1 | — | PASS | 2026-04-07. Headless Playwright: 44 images on meetings list, 0 broken. |
| 12 | Meetings list paginates (limit/offset/has_more) | 10 | — | T2 | dashboard/pagination | PASS | 2026-04-07. limit=3: PAGE1=3, has_more=true. PAGE2=3, no overlap. PAGINATION=PASS. |

**Confidence target:** 90

## Known bugs

| Bug | Status | Root cause |
|-----|--------|-----------|
| Meeting detail transcript empty on load + reload | **OPEN** | `getMeeting()` in `api.ts` returns raw API response. Field is `native_meeting_id` but dashboard uses `platform_specific_id`. Without `mapMeeting()`, value is `undefined`. Fix applied but not yet validated. |
| Dashboard credentials wrong after restart | **FIXED** | Compose defaulted `VEXA_ADMIN_API_KEY` to `vexa-admin-token` instead of `changeme`. VEXA_API_KEY stale. |
| REST transcript not fetched for active meetings | **FIXED** | `!shouldUseWebSocket` guard prevented REST fetch during active state. |
| Meeting icons broken | **FIXED** | PNG files removed from repo. Restored from git history. |
