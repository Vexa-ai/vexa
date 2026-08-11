# teams-capture/src

Front door [`index.ts`](index.ts). The browser pieces:
[`msteams-speakers.ts`](msteams-speakers.ts) (`createTeamsSpeakers` — watches the voice-level
"blue-square" outline + `vdi-frame-occlusion`, debounced speaking start/stop per participant + a ~2 s
heartbeat; OWNS the Teams selector arrays the bot re-exports) and
[`teams-chat.ts`](teams-chat.ts) (`createTeamsChat` — defensive chat-panel reader → `{ sender, text }`).

Zero external imports — pure DOM. The DOM scraping is live-validated in a real Teams.

[`msteams-captions.ts`](msteams-captions.ts) (`createTeamsCaptions`) reads Teams' LIVE CLOSED
CAPTIONS when they are on — a SECOND, independent speaker-attribution source, since Teams' own ASR
names the speaker. Selectors are ported from the 0.10 bot and belong to the same stable `data-tid`
family that survived the Fluent class-hash rot on the speaker side:
`[data-tid="closed-caption-renderer-wrapper"]` + `[data-tid="author"]` +
`[data-tid="closed-caption-text"]`, paired by DOCUMENT ORDER because the host view interposes an
items-renderer the guest view lacks. Entries emit on STABILIZATION, never per mutation (Teams
refines each caption in place). CC is treated as FLAKE-CLASS: availability transitions are typed
(`captions-active` / `captions-lost` / `captions-recovered` / `captions-absent`), recovery is
automatic, and the voice-level-outline watcher runs in parallel throughout — captions never replace
it. In this iteration caption events are DIAGNOSTIC: they feed neither the transcript nor the name
binder. Switching captions ON at join is the bot's job (a Playwright menu interaction in the capture
bridge), not this module's.

[`teams-capture.test.ts`](teams-capture.test.ts) (`npm test`) is the L2 unit: it drives the real chat
extraction (author/body, group-wrapper climb, aria + timestamp handling) against an in-memory DOM
shim and pins the exported selector arrays — no browser.
[`teams-captions.test.ts`](teams-captions.test.ts) is the caption reader's L2: host + guest caption
DOM, stabilization dedup, withheld-when-unresolved, absent-once, self-filter, teardown flush, and a
mid-meeting caption outage that recovers.
