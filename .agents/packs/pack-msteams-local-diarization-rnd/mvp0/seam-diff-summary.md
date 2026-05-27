# MVP0 — diarization seam diff summary

All MVP0 product-code changes live under one net-new subtree:

```
services/vexa-bot/rnd/diarization/        ← entire harness, net-new
├── package.json
├── tsconfig.json
├── README.md
├── scripts/
│   └── dev.sh
├── src/
│   ├── diarizer.ts                       ← the seam (Diarizer interface)
│   ├── stub-diarizer.ts                  ← VadRoundRobinDiarizer + RMS VAD
│   ├── transcription-client.ts           ← slim mirror of bot wire contract
│   ├── pipeline.ts                       ← per-speaker buffer + flush
│   ├── ws-protocol.ts                    ← browser ↔ harness types
│   └── server.ts                         ← HTTP + WebSocket entry point
└── public/
    ├── capture.html
    ├── capture.js
    ├── dashboard.html
    └── dashboard.js
```

## Files touched outside the harness subtree

**Zero.** No file under `services/vexa-bot/core/` was modified by MVP0.
No file in any other Vexa service was modified by MVP0.

## The seam (composition root)

`services/vexa-bot/rnd/diarization/src/diarizer.ts` defines the
`Diarizer` interface. `server.ts` constructs the chosen implementation
in exactly one place:

```ts
// services/vexa-bot/rnd/diarization/src/server.ts
const diarizer = new VadRoundRobinDiarizer({ numSpeakers: NUM_SPEAKERS });
//              ^^^^^^^^^^^^^^^^^^^^^^^^
//              MVP0: stub. Swap to PyannoteSidecarDiarizer at MVP1
//              by changing this one line.
```

`pipeline.ts` consumes the diarizer purely through the `Diarizer` interface —
no implementation-specific knowledge leaks downstream.

## What this enables for later MVPs

- **MVP1:** add `services/vexa-bot/rnd/diarization/src/pyannote-sidecar-diarizer.ts`
  implementing `Diarizer`, spawn the Python child process, swap the one
  line in `server.ts`. Pipeline + transcription + dashboard untouched.
- **MVP1+:** add `services/vexa-bot/rnd/diarization/src/file-replay-adapter.ts`
  alongside the tab adapter for autonomous metric runs.
- **MVP3:** add `diart-sidecar-diarizer.ts` / `nemo-sortformer-sidecar-diarizer.ts`
  behind the same interface. Backend comparison is one config change per run.
- **Stage 2:** the extractability audit decides how / whether to fold
  `Diarizer` into the production bot's `audio-pipeline.ts` /
  `speaker-streams.ts`. That work touches `services/vexa-bot/core/`;
  this MVP0 deliberately does not.
