# Bot Internal Services

Shared services used by all platform implementations. These handle the audio pipeline from browser capture through to Redis publishing.

## Pipeline Overview

```
Browser MediaStream
  -> audio.ts (per-speaker stream discovery from DOM media elements)
  -> vad.ts (Silero VAD silence filtering)
  -> speaker-streams.ts (confirmation buffer, 2s interval, 10s wall-clock cap)
  -> transcription-client.ts (HTTP POST to transcription-service)
  -> segment-publisher.ts (Redis XADD + PUBLISH)
```

## Modules

| Module | Role |
|--------|------|
| `audio.ts` | Per-speaker AudioContext pipelines. ScriptProcessorNode resamples to Float32 PCM. One `SpeakerStreamHandle` per media element. |
| `speaker-identity.ts` | Track-to-speaker voting: `LOCK_THRESHOLD=3`, `LOCK_RATIO=0.7`. One-name-per-track and one-track-per-name enforced. |
| `speaker-streams.ts` | Confirmation buffer: accumulates audio, resubmits every interval, emits only when transcript beginning stabilizes. Hard cap forces flush at 10s. |
| `transcription-client.ts` | HTTP POST to transcription-service with retries (default 3 attempts, 1s backoff). |
| `segment-publisher.ts` | Redis XADD to `transcription_segments` stream + PUBLISH speaker events. Segment ID format: `{session_uid}:{speakerId}:{seq}`. |
| `vad.ts` | Silero ONNX model via `onnxruntime-node`. Binary speech/silence filter. |
| `recording.ts` | WAV file accumulation in `/tmp`, upload via HTTP. |
| `chat.ts` | MutationObserver for chat messages, Redis storage. Can inject chat into transcription stream. |
| `hallucination-filter.ts` | Phrase files from `hallucinations/*.txt` + repetition detection. Multi-path resolution (dist/src/Docker). |

## Known Issues

- **Recording upload has no retry**: if the HTTP upload fails, the recording is silently lost. No retry mechanism.
- **VAD model file**: `silero_vad.onnx` must be present and loadable at runtime.
- **Hallucination phrase file paths**: resolution differs across dist/, src/, and Docker environments. Multi-path fallback handles this but can be fragile.
- **Confirmation buffer hard cap**: if transcription results never stabilize (e.g., noisy audio), the buffer hits the 10s hard cap every time, which may produce lower-quality segments.
