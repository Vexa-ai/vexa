# Browser Session

Persistent browser sessions with S3-backed profile sync. Lets users log into web apps (Google, Microsoft, etc.) once, and have that login survive across bot restarts and container re-creation.

## How It Works

1. Playwright launches Chromium with a persistent user data directory (`/browser-data`)
2. On startup, `s3-sync.ts` restores auth-essential files from S3
3. Every 60s + on graceful shutdown, auth-essential files are saved back to S3
4. User's login cookies, local storage, and session state persist across restarts

## Key Files

| File | Purpose |
|------|---------|
| `browser-session.ts` | Session lifecycle — launch browser, git workspace sync, auto-save loop |
| `s3-sync.ts` | S3 upload/download of browser profile data, lock management |
| `constans.ts` | Browser launch args (password-store=basic, etc.) |

## Known Issue: Session Cookies Lost on S3 Save

**Status:** needs fix, user-visible impact

**Problem:** Chromium only writes *persistent* cookies (those with an `Expires` or `Max-Age` header) to the `Default/Cookies` SQLite file on disk. Most login flows (Google, Microsoft, etc.) use *session* cookies — no expiry, valid until browser close. These live in memory only.

The S3 sync saves the `Cookies` file, but it only contains infrastructure cookies (Google tracking, consent). The actual login session cookies are never persisted to disk, so they're lost when the container restarts.

**Symptoms:**
- User logs into Google Meet via browser session
- Bot restarts (or auto-save fires)
- Login is gone — user must re-authenticate

**Current `AUTH_ESSENTIAL_FILES` (in `s3-sync.ts`):**
```
Local State
Default/Cookies
Default/Cookies-journal
Default/Login Data
Default/Login Data-journal
Default/Web Data
Default/Web Data-journal
Default/Local Storage/
Default/Session Storage/
Default/IndexedDB/
```

### Fix: CDP Cookie Export/Import

Before each S3 save, use Playwright's cookie API to export all cookies (including in-memory session cookies) to a JSON file. On restore, import them back.

**Implementation plan:**

1. **Before each S3 save** (auto-save every 60s + graceful shutdown):
   - Call `page.context().cookies()` to get all cookies including session cookies
   - Write to `Default/cdp-cookies.json`

2. **On browser session start**, after `launchPersistentContext`:
   - If `cdp-cookies.json` exists in restored profile, call `page.context().addCookies(...)` to restore session cookies

3. **Add to `AUTH_ESSENTIAL_FILES`** in `s3-sync.ts`:
   - `Default/cdp-cookies.json`

**Definition of done:**
- [ ] `fire_post_meeting_hooks` resolves real email from users table *(done: fe13226)*
- [ ] Before each S3 save, export all cookies via `page.context().cookies()` to `Default/cdp-cookies.json`
- [ ] On browser session start, restore cookies from `cdp-cookies.json` via `page.context().addCookies(...)`
- [ ] Add `Default/cdp-cookies.json` to `AUTH_ESSENTIAL_FILES` in `s3-sync.ts`
- [ ] Login persists across browser session restarts (manual test: log into Google, restart container, verify still logged in)
