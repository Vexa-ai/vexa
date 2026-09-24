# @vexa/telemost-capture — Telemost's contribution to the mixed lane (browser)

_meetings/ · module · Telemost page → `mixed-capture.v1` hints (the WHO signal)._

Runs **inside the meeting page**. Like Jitsi, Telemost delivers one mixed audio stream (captured by
[`@vexa/mixed-capture-core`](../mixed-capture-core/)), so this brick provides only the **WHO** signal
— no audio of its own:

- `installTelemostSignalTap` — installed at document start (the bot's init script, beside the WebRTC
  audio hook): observes the call frame's media-engine socket (goloom) — the roster (`id → name`, and each
  participant's `sendAudio`, whether their client is transmitting) and each participant slot's server-side
  `vad` flag from `slotsConfig`, which the engine re-sends on every change of that flag, in the grid and
  with a screen shared alike (the tiles' outline is drawn from it).
- `createTelemostSpeakers` — who is speaking, three readings in trust order: the tiles' speaking outline,
  the engine's slot VAD (names a speaker whose tile has no outline), and — only while those two have named
  nobody for `SENDING_FALLBACK_MS` — the roster's `sendAudio`, which needs no tile or slot at all (a share
  with no room for a strip). Emits speaking start/stop per participant → a `mixed-capture.v1`
  **hint** (kind `dom-active`). Several people can speak at once, so it tracks a set of speakers; a short
  release window rides out flicker, and a ~2 s heartbeat re-asserts a still-speaking participant. This
  module OWNS the Telemost tile selector arrays.

## Surface
`installTelemostSignalTap` · `telemostSignalState` · `applyTelemostSignal` · `createTelemostSpeakers` · `SENDING_FALLBACK_MS` · the selector arrays (`telemostTileSelectors`, `telemostSpeakingSelectors`,
`telemostTileNameSelectors`) (+ types `TelemostSpeakers`, `TelemostSpeakersOptions`, `SpeakerSource`).
Front door: [`src/index.ts`](src/index.ts).

## Verify
`pnpm --filter @vexa/telemost-capture run build` — `tsc` clean. The L2 unit drives the watcher against a
fake document (no browser); the selectors themselves are validated **live** in a real Telemost call.
`tsconfig` adds the `DOM` lib. Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
