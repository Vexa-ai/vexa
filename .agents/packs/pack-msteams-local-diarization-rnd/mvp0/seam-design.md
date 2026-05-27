# MVP0 — Diarization seam design

## Decision

The diarization seam is a **`Diarizer` interface** with a single concrete
implementation at MVP0:

```ts
export interface Diarizer {
  /** Per-frame call. Returns the current speaker label (e.g. "speaker_0"). */
  process(audio: Float32Array, timestampMs: number): Promise<string>;
  /** Reset state — call on new meeting / harness restart. */
  reset(): void;
}
```

Implementations:

| MVP  | Implementation                  | Backend                                                 |
| ---- | ------------------------------- | ------------------------------------------------------- |
| MVP0 | `VadRoundRobinDiarizer`         | reuses bot's `services/vad.ts` (Silero VAD via ONNX)    |
| MVP1 | `PyannoteSidecarDiarizer`       | Python child process holding pyannote 3.x streaming     |
| MVP1 | `PseudoOracleScriptDiarizer`    | (autonomous eval) reads pre-computed oracle script      |
| MVP3 | `DiartSidecarDiarizer` (alt)    | swap candidate for backend comparison                   |

The `Diarizer` interface is the **single composition root** for the swap.
All implementations are interchangeable — the harness picks one via env or
config and the rest of the pipeline (transcription-client + dashboard) is
agnostic.

## MVP0 stub: `VadRoundRobinDiarizer`

Logic:

1. Compute frame RMS energy.
2. Apply hysteresis: speech onset at `speechThreshold` (default 0.012),
   speech end after `minSilenceMs` (default 350ms) of frames below
   `silenceThreshold` (default 0.006).
3. On every silence→speech transition, advance speaker counter modulo N
   (default 2; configurable via `NUM_SPEAKERS`).
4. Emit `speaker_${counter}` as the current label.

This is **obviously a stub** — there's no actual voice discrimination. It
just rotates labels at every new speech turn. The point at MVP0 is to prove
the pipeline plumbing works end-to-end, not to diarize accurately.

Real diarization quality lands at MVP1 by swapping in
`PyannoteSidecarDiarizer` at the same composition root — pyannote brings
its own segmentation and embedding-based diarization in a Python child
process. The RMS-energy VAD inside this MVP0 stub is then retired.

## Why standalone harness, not direct bot integration at MVP0

The Vexa bot's production audio path goes:

```
Browser (Playwright) — MediaRecorder injection
       │ (page-context MediaRecorder → ondataavailable)
       ▼
audio-pipeline.ts — UnifiedRecordingPipeline (Layer 2)
       │ AudioChunk events
       ▼
recording.ts — uploadChunk to meeting-api
       │
       ▼
speaker-streams.ts — per-speaker sliding-window buffer
       │ Float32Array per speaker
       ▼
transcription-client.ts — POST /v1/audio/transcriptions
```

For MVP0's **tab-capture** use case, the user shares their own browser tab
(not a Playwright-driven bot session). So `MediaRecorderCapture` and
`PulseAudioCapture` don't apply — the audio source IS the tab capture
WebSocket. To plumb that into the full Pack U `UnifiedRecordingPipeline`
would require extracting the bot's lifecycle and meeting-api dependencies,
which is **the MVP3 extractability-audit deliverable**.

So MVP0 takes the pragmatic walking-skeleton cut:

- **Self-contained in the harness:** WebSocket audio ingest, `Diarizer`
  interface + `VadRoundRobinDiarizer` (with a local RMS-energy VAD), a
  slim transcription client mirroring the production bot's wire contract,
  per-speaker buffer + flush pipeline, dashboard.
- **Deferred to later MVPs:**
  - **MVP1:** swap `VadRoundRobinDiarizer` for `PyannoteSidecarDiarizer`
    (Python child process with pyannote 3.x). Brings real segmentation +
    embedding-based diarization. Also brings the bot's `vad.ts` Silero
    integration back into scope if useful.
  - **MVP3 extractability audit:** decide whether to share the production
    bot's `transcription-client.ts` / `vad.ts` directly with the harness or
    keep local mirrors. The harness's slim client today matches the
    production wire contract so this is a low-risk lift later.
  - **Stage 2:** wire the `Diarizer` into the bot's full Pack U
    `UnifiedRecordingPipeline` and `speaker-streams.ts` so production
    MS Teams meetings benefit.

The MVP0 harness importing the bot tree was attempted first; the bot's
`utils/index.ts` re-exports through `../utils` (a parallel `utils.ts`), and
`vad.ts` hardcodes ONNX model-path candidates that don't include the
harness's `node_modules`. Both are addressable but neither belongs in MVP0
walking-skeleton scope. The harness therefore mirrors the **wire contract**
(multipart `/v1/audio/transcriptions`) rather than the **module**. That is
explicit and honest, not a workaround.

## Harness layout

```
services/vexa-bot/rnd/diarization/
├── package.json                     # tsx + ws + express
├── tsconfig.json
├── README.md                        # how to run + scope honesty
├── src/
│   ├── server.ts                    # HTTP + WebSocket entry point
│   ├── diarizer.ts                  # Diarizer interface (the seam)
│   ├── stub-diarizer.ts             # VadRoundRobinDiarizer + RMS VAD
│   ├── transcription-client.ts      # slim client mirroring bot wire contract
│   ├── pipeline.ts                  # per-speaker buffer + flush + transcribe
│   └── ws-protocol.ts               # browser ↔ harness wire format
├── public/
│   ├── capture.html                 # tab-capture page (getDisplayMedia)
│   ├── capture.js                   # PCM capture + WS upload
│   ├── dashboard.html               # live speaker_N + transcript view
│   └── dashboard.js                 # WS subscriber + DOM render
└── scripts/
    └── dev.sh                       # npm install + tsx watch wrapper
```

The harness DOES NOT modify or import any file under
`services/vexa-bot/core/`. It is a net-new subtree under
`services/vexa-bot/rnd/diarization/`. This keeps the production bot
byte-identical for MVP0 and isolates the extractability question to MVP3.
