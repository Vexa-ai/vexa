# @vexa/gmeet-pipeline

The **gmeet lane** pipeline — the channel-router driver. Google Meet exposes
per-participant channels with the speaker name bound at capture, so this lane is
**overlap-safe and namer-free**: each channel transcribes independently and the
name rides on the audio.

```
gmeet-capture.v1 (named per-channel frames)
   └─ SpeakerStreamManager (channel router)
        ├─ per-channel sliding-window buffer + confirm (shared @vexa/transcribe-buffer)
        └─ @vexa/transcribe-whisper  stt.v1 transcribe (injected)
   ─► transcript.v1 (named segments)
```

## Surface
- `createGmeetPipeline(...)` → `transcript.v1`
- `SpeakerStreamManager` — per-channel buffers + confirm/flush (`addSpeaker()` MUST
  precede `feedAudio()` — it arms the submit timer)
- `isHallucination` (phrase filter) · `setLogger`

## Files
`src/gmeet-pipeline.ts`, `src/speaker-streams.ts`, `src/hallucination-filter.ts`
(+ `src/hallucinations/*.txt`), `src/log.ts`, `src/contracts/transcript-v1.ts`.

> **Deferred:** `speaker-streams` keeps its own confirm loop for now. The plan folds
> it into `@vexa/transcribe-buffer` (one shared confirm), but the two engines drifted
> and the buffer is currently tuned for the mixed lane (LocalAgreement-3) — that
> de-dup needs a gmeet golden first.

Gates: `npm run check:isolation` · `npm run build`.
