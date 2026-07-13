# @vexa/join — the isolated meeting-joining layer

_meetings/ · module (brick) · Google Meet + MS Teams + Zoom **web client** + Jitsi Meet._

**One concern:** drive a browser into a meeting and report its **admission verdict** —
nothing else. The embedder hands in a logged-in (or guest) Playwright `Page`; the brick
runs the platform's join flow and resolves once **admitted / rejected / timed-out**. It
**never** records, transcribes, talks to redis, or calls the meeting-api — those live
outside this boundary (`_host.ts` is the only seam, and it imports nothing but Node
builtins, so the package is provably standalone).

## Surface (front door — `src/index.ts`, P6)
- `joinMeeting(page, opts): Promise<{admitted, state}>` — the main entry; infers platform from the URL or takes `opts.platform`.
- `resolvePlatform(url)`, per-platform `join*/waitFor*Admission/leave*/start*RemovalMonitor`.
- `JOIN_BROWSER_ARGS` / `getJoinBrowserArgs()` — the canonical launch flags (so join↔bot flags never drift).
- `setHooks`, `startDebugView`; types `BotConfig`, `Hooks`, `JoinState`, `JoinOptions`, `JoinResult`.

## Depends on
`playwright` (the `Page`/automation) + Node builtins only. `playwright-extra` +
`puppeteer-extra-plugin-stealth` are used by the debug harness (`scripts/`). Verified by
`pnpm --filter @vexa/join check:isolation` (gate:isolation, P2). CommonJS by design — see
`tsconfig.json`; ESM consumers import it via Node interop.

## Prove it
- `pnpm --filter @vexa/join build` · `pnpm --filter @vexa/join test` (L1/L2 admission oracle — DOM fixtures, no browser)
- Live smoke (on a VM): `Dockerfile.debug` + noVNC — see `scripts/` and the `Makefile`.
