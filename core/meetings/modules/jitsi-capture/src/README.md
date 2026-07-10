# jitsi-capture/src

| Path | Concern |
|---|---|
| `index.ts` | front door: `createJitsiSpeakers` / `createJitsiChat` / `sendJitsiChatMessage` + selector exports |
| `jitsi-speakers.ts` | dominant-speaker watcher (redux primary, DOM fallback) → 'dom-active' hints |
| `jitsi-chat.ts` | chat reader (redux primary, DOM fallback) + send via the app's own API |
| `jitsi-capture.test.ts` | L2 — the observers driven against a fake `APP.store`, no browser |

Pure browser code — zero imports (ambient DOM), bundled standalone into the bot's page bundle.
