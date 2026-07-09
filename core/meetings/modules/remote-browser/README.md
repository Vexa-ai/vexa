# @vexa/remote-browser — browser-as-container + session persistence

_meetings/ · module · a VNC/CDP persistent browser whose login session is saved + retrievable._

One concern: a VNC/CDP-attachable **persistent** browser whose login session (cookies / localStorage /
Login Data) is saved and retrievable — so the join layer can be handed an already-authenticated page
(`BotConfig.authenticated`). Carved from `vexa-bot/core/src/{s3-sync,browser-session,constans}.ts`; the
bot now imports these instead of re-declaring them (one-way rule: services import bricks).

Two flows:

1. `provisionLogin()` — start browser + VNC → a human signs in → persist the session. Detects login by
   the page **leaving** the sign-in/OAuth pages (a reliable, non-disruptive signal), then confirms with
   `validateLoggedIn`.
2. `launchPersistentBrowser({ dataDir })` + `validateLoggedIn()` — restore + confirm.

Backends: S3 (`syncBrowserData{To,From}S3` — production, shells the `aws` CLI) or local
(`saveSessionLocal` / `loadSessionLocal`). Only the **auth-essential** subset of a Chromium profile is
persisted (~200 KB), not the full profile.

> Launch flags are deliberately restrained: NO `--disable-web-security` / `--ignore-certificate-errors`
> (Google's bot layer flags those → "You can't join this video call"), AutomationControlled disabled,
> NOT incognito (incognito wipes the stored cookies that make an authenticated join work). Session mode
> additionally carries the CDP debug args so an agent can attach over the gateway proxy.

## Surface
`provisionLogin` · `launchPersistentBrowser` · `validateLoggedIn` · `getAuthenticatedBrowserArgs` ·
`getBrowserSessionArgs` · `CDP_DEBUG_ARGS` · session store (`syncBrowserDataFromS3`/`…ToS3`,
`saveSessionLocal`/`loadSessionLocal`, `cleanStaleLocks`, `ensureBrowserDataDir`, `BROWSER_DATA_DIR`) ·
`AUTH_LOGIN_URLS` · `AUTH_COOKIES` (+ types `AuthPlatform`, `LoginStatus`, `S3Config`,
`LaunchPersistentOptions`, `ProvisionLoginOptions`). Front door: [`src/index.ts`](src/index.ts).

## Verify
`pnpm --filter @vexa/remote-browser run build` — `tsc` clean (self-contained CommonJS `tsconfig`:
extensionless relative imports + value-imports of `./types`, so it does NOT extend the ESM
`tsconfig.base`). [`src/auth.smoke.test.ts`](src/auth.smoke.test.ts)
(`pnpm --filter @vexa/remote-browser test`) pins the two contract-level invariants in isolation (no real
browser, no network): the launch-flag safety set, and the `validateLoggedIn` AND-matrix (loggedIn IFF
not bounced to a sign-in URL AND a known auth cookie is present) driven through a stub Playwright `Page`.
The real login / persistence / restore paths need an **integration env** (a headed Chromium + VNC, and
S3 creds for the S3 backend). Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
