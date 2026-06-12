# speaker-streams — the pipeline core brick

Consumes `capture.v1` audio (`feedAudio`) + speaker turns; emits attributed,
confirmed transcript segments. stt.v1 egress via `TranscriptionClient`
(OpenAI-compatible endpoint — any compatible implementation works).

- `src/speaker-streams.ts` — `SpeakerStreamManager`: per-speaker buffers,
  sliding-window submit, confirm/flush. **Contract: `addSpeaker()` MUST precede
  `feedAudio()` (it arms the submit timer).**
- `src/speaker-mapper.ts` — words × speaker boundaries → attributed segments.
- `src/vad.ts` — Silero VAD (onnxruntime-node).
- `src/hallucination-filter.ts` + `src/hallucinations/*.txt` — phrase filter.
- `src/transcription-client.ts` — stt.v1 client.
- `src/log.ts` — host-injectable logger (`setLogger`), the only host coupling.

## Harness
- **replay** (driver+oracle): `npm run replay -- <fixture-dir>` — feeds a recorded
  `capture.v1` fixture (per-speaker WAVs from the recorder brick) through the
  pipeline with no bot, no meeting, no GPU; prints the confirmed transcript.
  Proven against a live-recorded fixture: replay reproduces the live transcript.
- **unit oracle**: `npm test` (note: 2 pre-existing flaky assertions, inherited
  from the monolith — fail identically pre-extraction; tracked, not regressions).

Gates: `npm run check:isolation` · `npm run build` · `npm run replay`.
