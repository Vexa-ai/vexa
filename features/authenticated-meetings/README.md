# Authenticated Meetings

> Procs: `tests2/src/browser.md` (cookie save/restore), `tests2/src/bot.md` (authenticated join)

## What

Meeting bots can join Google Meet as a logged-in Google account instead of as an anonymous guest. This changes the join flow: authenticated bots see "Join now" instead of "Your name" + "Ask to join". They appear with the Google account's name and avatar, skip the name prompt, and may bypass waiting rooms that block anonymous guests.

This requires a prior browser session where a human logged into Google and saved the cookies to S3. The meeting bot then downloads those cookies and launches Chrome with a persistent context.

## Flow

```
[One-time setup]
POST /bots {mode: "browser_session"} → human logs into Google → POST /b/{token}/save
  → cookies saved to s3://{bucket}/users/{user_id}/browser-userdata/browser-data/

[Every meeting]
POST /bots {platform: "google_meet", native_meeting_id: "...", authenticated: true}
  │
  ├─ meeting-api adds S3 config to BOT_CONFIG:
  │    authenticated: true
  │    userdataS3Path: users/{user_id}/browser-userdata
  │    s3Endpoint, s3Bucket, s3AccessKey, s3SecretKey
  │
  ├─ Bot starts:
  │    syncBrowserDataFromS3() → download cookies to /tmp/browser-data/
  │    cleanStaleLocks() → remove SingletonLock/Cookie/Socket
  │    chromium.launchPersistentContext(BROWSER_DATA_DIR, {args: authArgs})
  │
  ├─ Bot navigates to meeting URL
  │
  ├─ Pre-join screen (authenticated):
  │    "Join now" → click → join directly (no name prompt, no "Ask to join")
  │    "Switch here" → same account already in call → click → switch device
  │    "Ask to join" → cookies didn't load → fallback: fill name, ask to join
  │
  └─ Bot in meeting as the Google account identity
```

## How it differs from anonymous join

| Aspect | Anonymous (default) | Authenticated (`authenticated: true`) |
|--------|-------------------|--------------------------------------|
| Pre-join screen | "Your name" input + "Ask to join" | "Join now" (or "Switch here") |
| Identity in meeting | Bot name from API request | Google account name + avatar |
| Waiting room | Must be admitted by host | May bypass (depends on meeting settings) |
| Browser context | Incognito (no state) | Persistent context with saved cookies |
| Chrome args | Standard `getBrowserArgs()` | `getAuthenticatedBrowserArgs()` — no incognito, `--password-store=basic` |
| S3 download | None | Downloads cookies from MinIO before launch |
| Cookie source | None | Same path as browser sessions: `users/{user_id}/browser-userdata/` |

## Chrome args (authenticated mode)

```
--no-sandbox
--disable-setuid-sandbox
--disable-blink-features=AutomationControlled
--disable-infobars
--disable-gpu
--use-fake-ui-for-media-stream
--use-file-for-fake-video-capture=/dev/null
--disable-features=VizDisplayCompositor
--password-store=basic
```

Key: `--password-store=basic` ensures Chrome uses a consistent cookie encryption key across containers. Without this, cookies encrypted in one container can't be decrypted in another.

No `--incognito` flag — the persistent context needs to read saved cookies.

## Fallback behavior

If cookies are expired, missing, or undecryptable, the bot falls back gracefully:

1. Bot navigates to meeting URL
2. Google shows "Ask to join" instead of "Join now"
3. Bot detects this: `WARNING: Authenticated mode but 'Ask to join' found instead of 'Join now'`
4. Bot fills the name input field with `botName`
5. Bot clicks "Ask to join" — same as anonymous flow
6. Bot enters waiting room (if meeting requires admission)

This is not silent failure — the warning is logged. The bot still joins, just as a guest.

## Teams and Zoom

**Teams:** No authenticated mode implemented. Teams bots always join as guests with the bot name. Enterprise Teams links (`teams.microsoft.com/l/meetup-join/...`) may block unauthenticated guests — this is an open gap.

**Zoom:** Not implemented.

## Components

| Component | File | Role |
|-----------|------|------|
| API flag | `services/meeting-api/meeting_api/schemas.py:571-573` | `authenticated: Optional[bool]` field on MeetingCreate |
| S3 config injection | `services/meeting-api/meeting_api/meetings.py:924-934` | Adds S3 credentials to BOT_CONFIG when `authenticated: true` |
| Cookie download | `services/vexa-bot/core/src/index.ts:2064-2078` | Downloads from S3, launches persistent context |
| S3 sync | `services/vexa-bot/core/src/s3-sync.ts` | `syncBrowserDataFromS3()` — aws s3 sync down |
| Authenticated join | `services/vexa-bot/core/src/platforms/googlemeet/join.ts:32-111` | Skip name input, handle Join now/Switch here/fallback |
| Chrome args | `services/vexa-bot/core/src/constans.ts:51-63` | `getAuthenticatedBrowserArgs()` |
| Cookie save (setup) | `services/vexa-bot/core/src/browser-session.ts:100-117` | `saveAll()` — saves cookies to S3 |

## Prerequisites

1. A browser session must have been created and logged into Google for this user
2. Cookies must have been saved to S3 (explicit save or auto-save)
3. The S3 path `users/{user_id}/browser-userdata/browser-data/Default/Cookies` must exist and be non-empty
4. `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` must be set in meeting-api's environment

## DoD

| # | Criterion | Weight | Ceiling | Status | Last |
|---|-----------|--------|---------|--------|------|
| 1 | `authenticated: true` adds S3 config to BOT_CONFIG | 10 | ceiling | PASS | 2026-04-07 |
| 2 | Bot downloads cookies from S3 before launching Chrome | 10 | ceiling | PASS | 2026-04-07 |
| 3 | Authenticated bot sees "Join now" on Google Meet (not "Ask to join") | 15 | ceiling | PASS | 2026-04-07 |
| 4 | Bot joins meeting as Google account identity (name + avatar) | 10 | — | PASS | 2026-04-07 |
| 5 | Fallback: expired cookies → bot falls back to anonymous join with warning | 10 | — | FAIL | 2026-04-07 |
| 6 | `--password-store=basic` ensures cookie decryption across containers | 10 | — | PASS | 2026-04-07 |
| 7 | Same S3 path works for both browser sessions and authenticated bots | 5 | — | PASS | 2026-04-07 |
| 8 | Bot logs diagnostic screenshot at auth lobby | 5 | — | PASS | 2026-04-07 |
| 9 | `use_saved_userdata` field silently dropped (schema field is `authenticated`) | 5 | — | PASS | 2026-04-07 |
| 10 | Teams authenticated join (enterprise links) | 10 | — | NOT IMPLEMENTED | |

## Known bugs

| Bug | Status | Evidence |
|-----|--------|----------|
| `use_saved_userdata` silently ignored | open | Schema field is `authenticated`; `MeetingCreate(extra="ignore")` drops unknown fields |
| Bot stuck on name input when unauthenticated | open | Bot waits 120s for name input selector when cookies didn't load. Should fail fast or fill name. |
| Teams enterprise links block unauthenticated guests | open | Org auth policies may reject anonymous bots. No authenticated Teams mode exists. |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| Bot sees "Ask to join" despite authenticated=true | Cookies expired or not downloaded | Bot logs warning and falls back to anonymous join | Fallback is graceful — bot still joins, just as guest |
| Bot stuck on pre-join screen, no button found | Google Meet UI changed, selectors stale | Diagnostic screenshot at `/app/storage/screenshots/bot-checkpoint-auth-lobby.png` | Always screenshot before throwing — the image tells you what Google changed |
| Cookies work in browser session but not in bot | Different Chrome args (incognito vs persistent) | Bot must use `getAuthenticatedBrowserArgs()` — no incognito flag | Incognito ignores saved cookies by design |
| "Switch here" button appears | Same Google account already in the meeting (e.g. host + bot) | Bot clicks "Switch here" — transfers the session to the bot | Not an error — expected when user is already in the meeting |
