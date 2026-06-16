# @vexa/transcribe-whisper

The shared **stt.v1 egress** — the single place that talks to Whisper. Both lanes
(gmeet channel-router, mixed segmenter) drive `@vexa/transcribe-buffer`, which calls
*this* through an injected `transcribe(pcm, prompt)` fn. So whisper knows nothing about
topology, naming, or confirmation — it is a pure `audio window → segments` function.

```
buffer ──stt.v1──► whisper ──► segments
         transcribe(pcm, prompt)
```

## Contract — `stt.v1`

`transcribe(pcm: Float32Array, language?, prompt?) → TranscriptionResult`
(`{ text, language, language_probability, segments[] }`, word-level timings). PCM is
16 kHz mono. Low-confidence / hallucinated segments are dropped at source
(`isLowConfidenceSegment`) before they ever reach the buffer.

## Public surface

| symbol | kind | role |
|---|---|---|
| `TranscriptionClient` | class | the stt.v1 client; `new TranscriptionClient(cfg)` |
| `client.transcribe(pcm, language?, prompt?)` | method | one Whisper round-trip → segments |
| `isLowConfidenceSegment(seg)` | fn | the STT-output junk filter (no_speech / logprob / compression) |
| `setLogger(fn)` | fn | host-injectable logger |
| `TranscriptionResult` / `…Segment` / `…Word` / `…ClientConfig` | types | the stt.v1 shapes |

## Naming conventions (project-wide)

This module follows the shared stage conventions:

- **construct**: `create<Name>(opts) → <Name>`; stateful pure logic = a `class`.
- **push data in**: `feed<Noun>(…)` (`feedAudio`, `feedFrame`, `feedHint`).
- **emit out**: one `on<X>` callback per output; a single `sink` object only when outputs
  are coupled (e.g. the transcript's `{ publish, publishPending, clearPending, rename }`).
- **serialize**: `encode<X>` / `decode<X>`.
- **stt**: `transcribe(pcm, prompt) → segments` (the whole stt.v1 surface — this module).
- **read state**: `get<X>()`.
- **teardown**: `dispose(): Promise<void>` if it must flush; `destroy(): void` for sync teardown.

## Isolation

A leaf brick — no `@vexa/*` deps. `npm run check:isolation` proves every import is
intra-package, a Node builtin, or a declared dep.
