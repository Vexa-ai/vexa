# @vexa/zoom-capture — Zoom's contribution to the mixed lane (browser)

_meetings/ · module · Zoom page → `mixed-capture.v1` hints (the WHO signal) + chat._

Runs **inside the meeting page**. Zoom mixes all participants into one audio stream (captured by
[`@vexa/mixed-capture-core`](../mixed-capture-core/)), so this brick provides only the **WHO** signal —
no audio of its own:

- `createZoomSpeakers` — polls Zoom's active-speaker DOM (~250 ms) and emits a name change on each
  transition → a `mixed-capture.v1` **hint** (`{ name, ts, isEnd }`, kind `dom-active`). Attribution
  is TEMPORAL (Zoom exposes only mixed audio, not per-participant `<audio>`): read who Zoom renders as
  the active speaker and label the mixed audio with that name. A ~2 s heartbeat re-asserts the current
  speaker so a consumer that started mid-turn learns who's talking without waiting for the next change.
  The downstream [`@vexa/mixed-pipeline`](../mixed-pipeline/) namer window-matches these hints against
  segmentation turns. `getState()` surfaces matched selectors + a tile survey for live selector tuning.
- `createZoomChat` — reads the chat panel (content tier); emits each new message as `{ sender, text }`.
- `createTrackNameResolver` — the **per-track** lane's channel↔name correlator. Zoom in fact delivers
  one WebRTC track per participant (witnessed live: 5 streams / 5 speakers, 0 remaps), so the bot can
  capture each participant on their own channel and skip the mix entirely. That makes the audio
  trustworthy and the NAME the unreliable part, which is the same shape Teams has and the opposite of
  Meet's (Meet hands the name over at turn onset). This is the resolver for it: vote/argmax binding,
  margin hysteresis, 1:1 by identity with a purity co-hold for genuinely duplicate names, idle release
  on rejoin, a sticky **self-exclusion** backstop, and stable **`Speaker A/B/C`** labels for channels
  that never earn a name. **Pure logic — no DOM, no audio** — so it is golden-testable offline, the
  way [`GmeetChannelBinder`](../gmeet-capture/) and [`TrackNamer`](../mixed-pipeline/) are.

**Two hosts, one brick** — the [bot](../../services/) reads `window.__vexaZoomSpeakers` (bundled into
its browser globals); the [extension](../../../clients/) imports it to label the mixed `tabCapture`
track. Selectors mirror the bot's Zoom `selectors.ts` and are defensive (Zoom's DOM shifts across builds).

## Surface
`createZoomSpeakers` · `createZoomChat` · `createTrackNameResolver` · `speakerLabel` ·
`TRACK_NAME_DEFAULTS` (+ types `ZoomSpeakers`, `ZoomChat`, `ZoomChatMessage`, `TrackNameResolver`,
`TrackNameResolverOptions`). Front door: [`src/index.ts`](src/index.ts).

## Verify
`pnpm --filter @vexa/zoom-capture run build` — `tsc` clean; `run test` chains the chat/active-speaker
unit and the resolver goldens. The DOM scraping (active-speaker + chat) is
validated **live** in a real Zoom (extension/bot) — consistent with how the lane has always been tested;
the resolver, being pure, is proved offline against its goldens instead.
`tsconfig` adds the `DOM` lib. Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
