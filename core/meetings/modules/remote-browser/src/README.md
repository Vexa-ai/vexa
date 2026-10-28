# remote-browser/src

Front door [`index.ts`](index.ts). The pieces:

- [`args.ts`](args.ts) — Chromium launch flags: `getAuthenticatedBrowserArgs` (persistent-context bot
  mode — minimal/clean, no detectable bypass flags, not incognito) and `getBrowserSessionArgs`
  (interactive VNC + CDP); `CDP_DEBUG_ARGS`.
- [`browser.ts`](browser.ts) — `launchPersistentBrowser`: the one true `launchPersistentContext` call
  (a persistent profile dir is what makes auth survive across launches).
- [`session-store.ts`](session-store.ts) — persist/retrieve the session profile; S3 backend (`aws`
  CLI) + local fs backend; lock hygiene. What the profile is: the
  [`session-profile.v1`](../../../contracts/session-profile.v1) contract (`SESSION_PROFILE` — exact files
  plus the LevelDB dirs' data files, with size limits), read from
  [`session-profile.v1.schema.json`](session-profile.v1.schema.json), a verbatim copy of the contract's
  schema held to it by `gate:fact-parity` (`session-profile-contract`). meeting-api reads the same file
  for its write-back route. Change the contract, then copy it over both. `writeSessionProfile` writes a
  profile received in memory (a restore answered over HTTP) into a profile dir: the whole set is
  checked first, and one entry that is not a profile path, is named twice or is over the limits
  refuses all of it (`SessionSyncError('session-restore')`).
- [`validate.ts`](validate.ts) — `validateLoggedIn`: navigate to the account page, decide from
  (not-on-sign-in-URL) AND (auth cookie present); `AUTH_LOGIN_URLS`, `AUTH_COOKIES`.
- [`login.ts`](login.ts) — `provisionLogin`: open VNC at the sign-in page, wait non-disruptively for the
  human, confirm, persist. `args` overrides the launch flags (default `getBrowserSessionArgs()`, which
  opens the CDP debug port); a caller that exposes only its VNC bridge passes a set without it.
- [`types.ts`](types.ts) — `AuthPlatform`, `LoginStatus`.

External imports: `playwright` + `playwright-extra` + Node builtins (child_process/fs/path). Pinned in
isolation by [`auth.smoke.test.ts`](auth.smoke.test.ts).
