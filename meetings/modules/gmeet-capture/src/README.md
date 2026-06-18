# gmeet-capture/src

Front door [`index.ts`](index.ts). The browser pieces:
[`pcm-capture.ts`](pcm-capture.ts) (per-element `AudioContext` → 16 kHz PCM),
[`gmeet-capture.ts`](gmeet-capture.ts) (rescan + per-channel wiring),
[`gmeet-speakers.ts`](gmeet-speakers.ts) (the live glow). The pure attribution logic:
[`gmeet-capture-v1.ts`](gmeet-capture-v1.ts) (the `capture.v1` producer + `pickBoundName`) and
[`gmeet-channel-binder.ts`](gmeet-channel-binder.ts) (energy↔glow correlation — DOM-free).

`gmeet-capture.test.ts` is the pure-core golden (`gate:node`); the DOM paths are live-validated.
