# jitsi-capture/src

Front door [`index.ts`](index.ts). The browser pieces:
[`jitsi-speakers.ts`](jitsi-speakers.ts) (`createJitsiSpeakers` — dominant-speaker watcher, redux
primary + `.dominant-speaker` tile DOM fallback, exclusive global transitions + a ~2 s liveness
heartbeat; OWNS the jitsi tile selector arrays) and
[`jitsi-chat.ts`](jitsi-chat.ts) (`createJitsiChat` — redux-primary chat reader → `{ sender, text }`;
`sendJitsiChatMessage` posts via the app's own API).

Zero external imports — pure browser code (ambient DOM), bundled standalone into the bot's page bundle.

[`jitsi-capture.test.ts`](jitsi-capture.test.ts) (`npm test`) is the L2 unit: it drives both observers
against a fake `APP.store` and pins the exported selector arrays — no browser.
