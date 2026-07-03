# remote-browser/src

Front door [`index.ts`](index.ts). The pieces:

- [`args.ts`](args.ts) — Chromium launch flags: `getAuthenticatedBrowserArgs` (persistent-context bot
  mode — minimal/clean, no detectable bypass flags, not incognito) and `getBrowserSessionArgs`
  (interactive VNC + CDP); `CDP_DEBUG_ARGS`.
- [`browser.ts`](browser.ts) — `launchPersistentBrowser`: the one true `launchPersistentContext` call
  (a persistent profile dir is what makes auth survive across launches).
- [`session-store.ts`](session-store.ts) — persist/retrieve the auth-essential profile subset; S3
  backend (`aws` CLI) + local fs backend; lock hygiene.
- [`validate.ts`](validate.ts) — `validateLoggedIn`: navigate to the account page, decide from
  (not-on-sign-in-URL) AND (auth cookie present); `AUTH_LOGIN_URLS`, `AUTH_COOKIES`.
- [`login.ts`](login.ts) — `provisionLogin`: open VNC at the sign-in page, wait non-disruptively for the
  human, confirm, persist.
- [`types.ts`](types.ts) — `AuthPlatform`, `LoginStatus`.

External imports: `playwright` + `playwright-extra` + Node builtins (child_process/fs/path). Pinned in
isolation by [`auth.smoke.test.ts`](auth.smoke.test.ts).
