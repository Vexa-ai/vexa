# zoom-capture/src

Front door [`index.ts`](index.ts). The browser pieces:
[`zoom-speakers.ts`](zoom-speakers.ts) (`createZoomSpeakers` — polls Zoom's active-speaker DOM,
emits a name on each transition + a ~2 s heartbeat; selectors mirror the bot's Zoom `selectors.ts`,
with `getState()` forensics for live selector tuning) and
[`zoom-chat.ts`](zoom-chat.ts) (`createZoomChat` — defensive chat-panel reader → `{ sender, text }`).

Zero external imports — pure DOM. The DOM scraping is live-validated in a real Zoom.

[`zoom-capture.test.ts`](zoom-capture.test.ts) (`npm test`) is the L2 unit: it drives the real chat
extraction (sender/body, group-header climb, aria + timestamp handling) and the active-speaker
flicker-confirmation against an in-memory DOM shim — no browser.
