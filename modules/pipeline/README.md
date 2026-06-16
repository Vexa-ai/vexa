# @vexa/pipeline — the gmeet pipeline + shared host remnant (TRANSITIONAL)

The **gmeet (channel-routed) pipeline**: `capture.v1` (per-channel audio + glow
name) → `transcript.v1`. Overlap-safe — separate channels transcribe
independently; the glow name labels each turn, bound at onset.

> **Transitional.** The mixed lane moved out to `@vexa/mixed-pipeline` (segmenter
> + hints-namer, no diarization). The gmeet lane here will be carved into
> `modules/gmeet/pipeline` (`@vexa/gmeet-pipeline`); after that this package
> retires. The diarizer monolith, online/wespeaker clustering, `createMixedPipeline`
> and `separated-transcript.v1` have been **deleted** (per plan).

## Surface
- `createGmeetPipeline(...)` — the channel-router driver → `transcript.v1`
- `SpeakerStreamManager` — per-channel buffers + confirm/flush
- `SileroVAD` (onnxruntime-node) · `isHallucination` · `setLogger`
- Re-exports `TranscriptionClient` from `@vexa/transcribe-whisper` (stt.v1)

## Files
`src/gmeet-pipeline.ts`, `src/speaker-streams.ts`, `src/vad.ts`,
`src/hallucination-filter.ts` (+ `src/hallucinations/*.txt`), `src/log.ts`,
`src/contracts/transcript-v1.ts`.

## Hosts (live in `scripts/` so the heavy node_modules resolve here)
- `npm run dev` → `scripts/desktop.ts` — the Vexa Desktop backend (ingest WS 9099
  + gateway 8056). Runs the gmeet pipeline **and** the mixed lane via
  `@vexa/mixed-pipeline` + `@vexa/capture-codec`.
- `npm run live-stack` → `scripts/live-stack.ts` — sidepanel live stack.
- `npm run replay` → `scripts/fixture-replay.ts` · `npm run e2e` → `scripts/fixture-feed.ts`.

Mixed-lane evaluation moved to `modules/mixed/eval` (YouTube fixture vs our
pipeline, Deepgram reference).

Gates: `npm run check:isolation` · `npm run build`.
