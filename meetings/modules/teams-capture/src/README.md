# teams-capture/src

Front door [`index.ts`](index.ts). The browser pieces:
[`msteams-speakers.ts`](msteams-speakers.ts) (`createTeamsSpeakers` — watches the voice-level
"blue-square" outline + `vdi-frame-occlusion`, debounced speaking start/stop per participant + a ~2 s
heartbeat; OWNS the Teams selector arrays the bot re-exports) and
[`teams-chat.ts`](teams-chat.ts) (`createTeamsChat` — defensive chat-panel reader → `{ sender, text }`).

Zero external imports — pure DOM. The DOM scraping is live-validated in a real Teams.
