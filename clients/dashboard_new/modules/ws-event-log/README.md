# @vexa/dash-ws-event-log

_dashboard_new brick · the live WS frame-log **debug VIEW**._

A presentational React component that renders an injected list of WebSocket frames as a terminal-style
stream — one row per frame, **newest first**, each row showing the frame `type` tag and a one-line
`summary`. It is the modular, decoupled replacement for the vendored dashboard's
`components/meetings/ws-event-log.tsx`, which synthesized fake events from `status`/`segmentCount` and
hardcoded `process.env` API URLs. This brick does none of that: **props in, DOM out.**

## What it is — and is NOT

- **It IS** presentational: it renders the rows it is handed. Nothing else.
- **It is NOT** a store, a fetcher, or a WebSocket. It holds no state beyond a memoized reverse of the
  input, makes no network calls, and opens no socket. Whoever owns the live WS (e.g. `@vexa/dash-ws`
  driving `@vexa/dash-meeting-state`) turns raw `ws.v1` frames into `{ ts, type, summary }` rows and
  passes them down. That seam is what keeps this brick free of the vendored view's coupling.
- It is typed against the **`@vexa/dash-contracts`** WS vocabulary (the sealed `ws.v1` `type` tags).

## Props contract

```ts
import { WsEventLog } from "@vexa/dash-ws-event-log";
import type { WsLogEvent } from "@vexa/dash-ws-event-log";

interface WsLogEvent {
  ts?: string;                       // row timestamp, e.g. "10:00:05". Optional — omit to hide it.
  type: WsFrameType | (string & {}); // the frame's `type` tag (modeled ws.v1 tag, or any forwarded one)
  summary: string;                   // a one-line human summary, e.g. "status: active", "Alice: hi"
}

interface WsEventLogProps {
  events: WsLogEvent[];  // the frames to render. Rendered NEWEST FIRST regardless of input order.
  title?: string;        // optional chrome-bar heading. Defaults to "WebSocket".
}

function WsEventLog(props: WsEventLogProps): JSX.Element;
```

- **`events`** — the only required prop. The component renders them **newest first** (it reverses a
  copy; the caller's array is never mutated). An empty array renders an empty-state placeholder.
- **`type`** — `WsFrameType` is `WsFrame["type"]` from `@vexa/dash-contracts` (`meeting.status`,
  `transcript`, `transcription_segment`, `chat_message`, `subscribed`, `unsubscribed`, `pong`,
  `error`). It is widened with `string` because the gateway forwards the raw redis payload verbatim and
  frames are **additive** — a producer may emit a tag the dashboard doesn't model (e.g.
  `transcript.mutable`). Such rows still render; their colour falls back to the "other" family.
- **`summary`** — pre-computed by the caller. The brick does not parse frame bodies; it only paints the
  string. This keeps frame-shape knowledge in the WS layer, not the view.

No CSS framework is required: colours/layout are inline styles, so the component is drop-in anywhere.

## DOM contract (what the L4 spec asserts)

| selector                                | meaning                                              |
| --------------------------------------- | ---------------------------------------------------- |
| `[data-testid="ws-event-log"]`          | the root container                                   |
| `[data-testid="ws-event-row"]`          | one per event, in render order (**newest first**)    |
| `[data-testid="ws-event-type"]`         | the frame's `type` tag (inside a row)                |
| `[data-testid="ws-event-summary"]`      | the frame's `summary` (inside a row)                 |
| `[data-testid="ws-event-ts"]`           | the row timestamp (present only when `ts` is set)    |
| `[data-testid="ws-event-empty"]`        | shown instead of rows when `events` is empty         |
| `[data-testid="ws-event-count"]`        | the `N events` footer count                          |

Each row also carries `data-kind` (`control` / `status` / `transcript` / `chat` / `error` / `other`)
for styling/inspection.

## Build & test

The exit-code signal for this VIEW brick is an **L4 gate**: a real chromium (Playwright) mounts the
component over golden events and asserts the rendered DOM. A green node/jsdom test can be a false-green
for a human; a green chromium render cannot.

```bash
# from this brick dir
npm install
npx playwright install chromium   # once, if the browser isn't cached
npm run build                     # tsc → dist/ (typecheck + emit)
npm test                          # esbuild-bundles the component + fixture, runs the Playwright L4 gate
```

`npm test` runs `playwright test --config e2e/playwright.config.ts`. Its `globalSetup`
(`e2e/build-bundle.mjs`) esbuild-bundles the **real** component source (`src/index.ts`) plus
react/react-dom into the fixture, so the gate always exercises the current brick. `e2e/fixtures/golden.js`
is the single source of truth for the injected events and the expected rows — imported by both the
fixture page and the spec so they cannot drift.

The gate is verified to be a real signal: breaking the newest-first ordering in `WsEventLog.tsx` makes
the spec fail at the row assertion; restoring it passes.

## Files

```
src/index.ts          the single front door — exports WsEventLog + WsEventLogProps + WsLogEvent + WsFrameType
src/WsEventLog.tsx     the presentational component (props in, DOM out)
src/types.ts          WsLogEvent / WsFrameType, anchored on @vexa/dash-contracts' WsFrame
e2e/                  the L4 Playwright render harness (build-bundle, static-server, config, spec, fixtures)
```
