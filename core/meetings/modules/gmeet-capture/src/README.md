# gmeet-capture/src

Front door [`index.ts`](index.ts). The browser pieces:
[`pcm-capture.ts`](pcm-capture.ts) (per-element `AudioContext` → 16 kHz PCM via the `WORKLET_SRC`
AudioWorklet — loaded from a host-supplied `moduleUrl` under MV3, else a `blob:` URL),
[`gmeet-capture.ts`](gmeet-capture.ts) (rescan + per-channel wiring),
[`gmeet-speakers.ts`](gmeet-speakers.ts) (the live glow),
[`gmeet-chat.ts`](gmeet-chat.ts) (the in-call chat reader + sender). The pure attribution logic:
[`gmeet-capture-v1.ts`](gmeet-capture-v1.ts) (the `capture.v1` producer + `pickBoundName`) and
[`gmeet-channel-binder.ts`](gmeet-channel-binder.ts) (energy↔glow correlation — DOM-free).

`gmeet-capture.test.ts` is the pure-core golden (`pickBoundName` + the energy↔glow channel binder);
`gmeet-speakers.test.ts` is the L2 unit for the glow→START/END hint edges, self-tile suppression, and
junk-name filtering (in-memory DOM shim); `gmeet-chat.test.ts` pins the chat reader (priming,
emit-once, grouped sender, own-line skip) and sender against a chat-panel DOM fixture;
`gmeet-chat-locales.test.ts` runs the reader, sender and lookups over the `fixtures/meet-chat-<lang>.html`
panels (en, nl, de, and a label-free variant) in jsdom and pins that no English label is needed. All run on
`npm test` (`gate:node`); the DOM paths are live-validated.
