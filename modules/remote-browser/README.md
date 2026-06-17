# @vexa/remote-browser

**The browser as a container with a memory.** One concern: a VNC/CDP-attachable
**persistent** browser whose login session (cookies / localStorage / Login Data) is
**saved** and **retrievable** — so the join layer can be handed an already-authenticated
page instead of joining as an anonymous guest.

## Why this exists

Anonymous web joins hit walls — most visibly Zoom's *"sign in to join / automated bots
aren't allowed"* page. The fix Zoom itself asks for is to **be signed in**. This module
makes "signed in" a durable, reusable thing: a human logs in **once** over VNC, the
session is persisted, and every later bot launch restores it and joins authenticated.

Carved (single source of truth) from `services/vexa-bot/core/src/{s3-sync.ts,
browser-session.ts, constans.ts}` — the bot now imports this instead of re-declaring it.

## The two flows

```
1. LOGIN (provision, once)        2. RESTORE + VALIDATE (every run)
   provisionLogin({platform})        launchPersistentBrowser({dataDir: profile})
   → launch browser + VNC            → validateLoggedIn(page, platform)
   → human signs in (noVNC :6080)    → { loggedIn: true } → hand page to the join layer
   → poll auth cookie → save
```

## API

| Export | Purpose |
|---|---|
| `launchPersistentBrowser({dataDir, args, headless?})` | the one true persistent-context launch → `{context, page}` |
| `getAuthenticatedBrowserArgs()` / `getBrowserSessionArgs()` | clean persistent-context flags (NOT incognito — it would wipe cookies) |
| `provisionLogin({platform, profileDir, backupDir?})` | open VNC, wait for human login, persist |
| `validateLoggedIn(page, platform)` | `{loggedIn, detail}` — heuristic: account-page URL + auth cookie |
| `syncBrowserData{To,From}S3` / `save`/`loadSessionLocal` | persist/retrieve backends (S3 = prod, local = desktop/dev) |
| `cleanStaleLocks`, `ensureBrowserDataDir`, `BROWSER_DATA_DIR` | profile hygiene |

`AuthPlatform = 'zoom' | 'google' | 'teams'`.

## The seam to the join layer

The session lives in the Chromium **profile dir** (`dataDir`). Restore it, launch, and
hand the authenticated `page` to `joinMeeting(page, { authenticated: true })` — the join
brick already branches on `authenticated` (skips guest name-entry, uses the account identity).

## Run the flows (needs the VNC env image)

```bash
PLATFORM=zoom PROFILE=/data/profiles/myzoom tsx scripts/login.ts      # log in via noVNC :6080
PLATFORM=zoom PROFILE=/data/profiles/myzoom tsx scripts/validate.ts   # exits 0 if still logged in
```

## Gates

```bash
npm run build              # tsc clean
npm run check:isolation    # no import escapes the package
```

## Status / open

- `validateLoggedIn` cookie names + sign-in URL markers (`src/validate.ts`) are
  best-effort — tighten per platform once observed live.
- A `Dockerfile` + `Makefile` harness (FROM the join env image: Xvfb + noVNC) to run
  `scripts/login.ts` / `validate.ts` in-container is the next step.
