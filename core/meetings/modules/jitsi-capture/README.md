# @vexa/jitsi-capture — Jitsi's contribution to the mixed lane (browser)

_meetings/ · module · Jitsi page → `mixed-capture.v1` hints (the WHO signal) + chat._

Runs **inside the meeting page**. Like Zoom and Teams, Jitsi delivers one mixed audio stream (captured
by [`@vexa/mixed-capture-core`](../mixed-capture-core/)), so this brick provides only the **WHO** signal
and chat — no audio of its own:

- `createJitsiSpeakers` — watches the app's own dominant-speaker state (`APP.store` redux — what
  jitsi's UI renders from; `.dominant-speaker` tile DOM fallback for builds that strip the global) and
  emits speaking start/stop per participant → a `mixed-capture.v1` **hint** (kind `dom-active`). A ~2 s
  heartbeat re-asserts the still-dominant speaker so a consumer that started mid-turn learns who's
  talking without waiting for the next transition. This module OWNS the jitsi selector arrays.
- `createJitsiChat` — reads the conference chat (redux-primary, so the panel need **not** be open; DOM
  fallback otherwise); emits each new message as `{ sender, text }`.
- `sendJitsiChatMessage` — posts into the conference chat via the app's own `sendTextMessage` API.

## Surface
`createJitsiSpeakers` · `createJitsiChat` · `sendJitsiChatMessage` · the selector arrays
(`jitsiDominantTileSelectors`, `jitsiTileNameSelectors`, `jitsiChatContainerSelectors`,
`jitsiChatMessageSelectors`, `jitsiChatSenderSelectors`, `jitsiChatTextSelectors`) (+ types
`JitsiSpeakers`, `JitsiSpeakersOptions`, `JitsiChat`, `JitsiChatMessage`, `JitsiChatOptions`).
Front door: [`src/index.ts`](src/index.ts).

## Verify
`pnpm --filter @vexa/jitsi-capture run build` — `tsc` clean. The L2 unit drives both observers against
a fake `APP.store` (no browser); the DOM fallbacks and live behavior are validated **live** in a real
Jitsi meeting — consistent with how the lane has always been tested. `tsconfig` adds the `DOM` lib.
Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
