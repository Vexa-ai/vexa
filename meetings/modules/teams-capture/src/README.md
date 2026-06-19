# teams-capture/src

Front door [`index.ts`](index.ts). The browser pieces:
[`msteams-speakers.ts`](msteams-speakers.ts) (`createTeamsSpeakers` — watches the voice-level
"blue-square" outline + `vdi-frame-occlusion`, debounced speaking start/stop per participant + a ~2 s
heartbeat; OWNS the Teams selector arrays the bot re-exports) and
[`teams-chat.ts`](teams-chat.ts) (`createTeamsChat` — defensive chat-panel reader → `{ sender, text }`).

Zero external imports — pure DOM. The DOM scraping is live-validated in a real Teams.

[`teams-capture.test.ts`](teams-capture.test.ts) (`npm test`) is the L2 unit: it drives the real chat
extraction (author/body, group-wrapper climb, aria + timestamp handling) against an in-memory DOM
shim and pins the exported selector arrays — no browser.
