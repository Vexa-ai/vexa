# @vexa/jitsi-capture — Jitsi's WHO + chat signals for the mixed lane

_meetings/ · module (brick) · pure browser code (bundled into the bot's page bundle)._

**One concern:** the Jitsi-specific page-side signals the mixed capture lane can't get
from audio alone. Audio itself comes from `@vexa/mixed-capture-core` (the WebRTC hook —
platform-agnostic); this brick contributes:

- `createJitsiSpeakers` — dominant-speaker watcher → `onSpeaking(name, id, isEnd, tMs)`
  (the mixed lane's 'dom-active' naming hints, same protocol as `@vexa/teams-capture`).
  Reads the app's own redux state (`APP.store` — what jitsi's UI renders from), with a
  DOM fallback (`.dominant-speaker` tile) for builds that strip the global.
- `createJitsiChat` — chat reader → `onMessage({sender, text})`. Redux-primary, so the
  chat panel need NOT be open (an advantage over the Teams/Zoom DOM readers); DOM
  fallback otherwise.
- `sendJitsiChatMessage(text)` — posts into the conference chat via the app's own
  `sendTextMessage` API.

## Depends on
Nothing (ambient DOM only; devDeps for build/test). Verified by `check:isolation`
(gate:isolation, P2). ESM; consumed via the front door `index.ts` (P6).

## Prove it
`pnpm --filter @vexa/jitsi-capture build · test` — L2 drives the real observers against
a fake `APP.store` (no browser). The DOM fallbacks and live behavior are L4 (a real
meeting), same obligation as every capture brick.
