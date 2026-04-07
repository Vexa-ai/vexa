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
| 1 | Dashboard container reaches all backends (wget) | 10 | — | T1 | dashboard/3 | PASS | 2026-04-07 |
| 2 | No false "failed" for successful meetings | 10 | — | T1 | dashboard/4 | PASS | 2026-04-07 |
| 3 | Magic link login returns 200 + sets cookie | 10 | ceiling | T2 | dashboard/5 | PASS | 2026-04-07 |
| 4 | Meetings list loads with auth cookie | 10 | ceiling | T2 | dashboard/6 | PASS | 2026-04-07 |
| 5 | GET /meetings/{id} returns native_meeting_id (field contract) | 10 | ceiling | T2 | dashboard/7 | FAIL (fix applied, not validated) | 2026-04-07 |
| 6 | Transcript via proxy returns segments | 10 | — | T2 | dashboard/8 | PASS | 2026-04-07 |
| 7 | Meeting page renders transcript in browser (headless) | 15 | ceiling | T3 | dashboard/9 | UNTESTED | |
| 8 | Meeting page shows correct status (matches API) | 10 | — | T3 | dashboard/10 | UNTESTED | |
| 9 | Cache headers prevent stale JS bundles | 5 | — | T3 | dashboard/11 | UNTESTED | |
| 10 | Dashboard credentials valid (VEXA_ADMIN_API_KEY, VEXA_API_KEY) | 10 | ceiling | T1 | infra/11 | PASS | 2026-04-07 |
| 11 | Platform icons render (no broken images) | 5 | — | T1 | — | PASS | 2026-04-07 |

**Confidence target:** 90

## Known bugs

| Bug | Status | Root cause |
|-----|--------|-----------|
| Meeting detail transcript empty on load + reload | **OPEN** | `getMeeting()` in `api.ts` returns raw API response. Field is `native_meeting_id` but dashboard uses `platform_specific_id`. Without `mapMeeting()`, value is `undefined`. Fix applied but not yet validated. |
| Dashboard credentials wrong after restart | **FIXED** | Compose defaulted `VEXA_ADMIN_API_KEY` to `vexa-admin-token` instead of `changeme`. VEXA_API_KEY stale. |
| REST transcript not fetched for active meetings | **FIXED** | `!shouldUseWebSocket` guard prevented REST fetch during active state. |
| Meeting icons broken | **FIXED** | PNG files removed from repo. Restored from git history. |
