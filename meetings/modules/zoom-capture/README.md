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

**Two hosts, one brick** — the [bot](../../services/) reads `window.__vexaZoomSpeakers` (bundled into
its browser globals); the [extension](../../../clients/) imports it to label the mixed `tabCapture`
track. Selectors mirror the bot's Zoom `selectors.ts` and are defensive (Zoom's DOM shifts across builds).

## Surface
`createZoomSpeakers` · `createZoomChat` (+ types `ZoomSpeakers`, `ZoomChat`, `ZoomChatMessage`).
Front door: [`src/index.ts`](src/index.ts).

## Verify
`pnpm --filter @vexa/zoom-capture run build` — `tsc` clean. The DOM scraping (active-speaker + chat) is
validated **live** in a real Zoom (extension/bot) — consistent with how the lane has always been tested.
`tsconfig` adds the `DOM` lib. Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
