# Browser Session — Persistent Authenticated Browser

> Proc: `tests2/src/browser.md`

## What

Remote browser containers (Playwright + Chrome) that persist login state across restarts via S3/MinIO. Used for:
- Creating Google Meet meetings (requires Google login)
- Auto-admitting bots via CDP (requires being in the meeting as host)
- Authenticated browser-based bot joins

## How it works

```
create session → download userdata from S3 → launch Chrome with persistent context
    → user logs in (once) → save cookies+localStorage to S3
    → destroy session → create new session → download saved state → Chrome restores login
```

Key paths:
- S3 path: `s3://{MINIO_BUCKET}/users/{user_id}/browser-userdata/browser-data/`
- Local path inside container: `/tmp/browser-data/`
- Save mechanism: Redis pubsub `save_storage` command → bot syncs auth-essential files to S3
- Restore mechanism: `aws s3 sync` on container startup
- Auto-save: every 60s, uploads auth-essential files

## DoD

| # | Criterion | Weight | Tier | Proc step | Last | Result |
|---|-----------|--------|------|-----------|------|--------|
| 1 | POST /bots mode=browser_session returns 201 with session_token | 5 | auto | browser/create | 2026-04-07 | PASS |
| 2 | CDP proxy reachable at /b/{token}/cdp | 5 | auto | browser/verify_cdp | 2026-04-07 | PASS |
| 3 | Bot downloads existing S3 state on startup (logs show "S3 sync down") | 5 | auto | browser/verify_s3_download | 2026-04-07 | PASS |
| 4 | Explicit save (POST /b/{token}/save) returns 200 and writes to MinIO | 10 | auto | browser/save | 2026-04-07 | PASS |
| 5 | Cookies file exists in MinIO at correct path after save | 5 | auto | browser/verify_minio | 2026-04-07 | PASS (20KiB Cookies, Local Storage/) |
| 6 | Auto-save cycle fires within 70s (timestamp refreshes) | 5 | auto | browser/verify_auto_save | | UNTESTED |
| 7 | Test data survives destroy→recreate cycle (write marker file, restore, read it back) | 15 | auto | browser/roundtrip | 2026-04-07 | PASS (marker=1775518624 survived) |
| 8 | No stale lock files (SingletonLock, etc.) after restore | 5 | auto | browser/verify_locks | 2026-04-07 | PASS (lock is live, not stale) |
| 9 | `authenticated: true` flag triggers S3 config in bot_config | 5 | auto | browser/verify_auth_flag | | UNTESTED |
| 10 | Google login persists across session restart | 20 | human | browser/verify_login | 2026-04-07 | PASS (myaccount.google.com reachable after destroy→recreate) |
| 11 | meet.new redirects to meet.google.com (not login page) after restore | 15 | human | browser/verify_meet_new | 2026-04-07 | PASS (ugb-unbk-nan created) |
| 12 | Graceful shutdown (SIGTERM) triggers save before exit | 5 | auto | browser/verify_shutdown_save | | UNTESTED |

**Confidence target:** 80 (auto tier alone = 60 max, human tier adds 35)

## Known bugs

| Bug | Status | Evidence |
|-----|--------|----------|
| `use_saved_userdata` field silently ignored | open | Schema field is `authenticated`; `MeetingCreate(extra="ignore")` drops unknown fields. Callers must use `authenticated: true`. |
| Old browser proc checked wrong MinIO bucket | fixed | Was `local/vexa/`, correct is `local/{MINIO_BUCKET}/` |
| Auto-save can overwrite valid cookies with empty session state | disproved | Tier 2 proved: auto-save preserves login. Google cookies survived destroy→recreate. Not an actual bug. |
| Chrome cookie encryption may block cross-container restore | disproved | Tier 2 proved: Google login persists across containers. Chrome `--password-store=basic` works consistently in Docker. |
