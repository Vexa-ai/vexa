# @vexa/gmeet-pipeline — the gmeet lane (channel-routed)

_meetings/ · module · `gmeet-capture.v1` (named per-channel audio) → `transcript.v1`._

Google Meet delivers each active speaker on a **separate channel**, so audio is
routed by channel: two people talking at once land on separate per-channel streams
and are transcribed **independently** — no muddling, onsets intact. The glow names
each channel-**turn**, bound at the turn's onset and held through overlap. Identity
is **carried** (bound at capture), never derived — no diarizer, no post-hoc namer.
Contrast [`mixed-pipeline`](../) (one mixed stream, names from hints).

Each `(channel, turn)` is its own stream over the shared engine
([`buffer`](../buffer/) LocalAgreement + [`whisper`](../whisper/) stt.v1, injected),
emitting **sealed `transcript.v1`** segments to a `TranscriptSink`. The host wraps
those into the bus envelopes.

## Surface
`createGmeetPipeline` · `SpeakerStreamManager` · `isHallucination` · `setLogger` ·
types `GmeetPipeline(Options)`, `TranscriptSegment`, `TranscriptSink`, `Source`.
Front door: [`src/index.ts`](src/index.ts).

## Verify
```bash
pnpm --filter @vexa/gmeet-pipeline build
pnpm --filter @vexa/gmeet-pipeline test
```
Two goldens, both **offline** (no transcription-service):
- `hallucination-filter.test.ts` — phrase-list + structural junk drop.
- `pipeline-conformance.test.ts` — **the replay gate**: drive the pipeline with a
  stub Whisper and validate every emitted segment against the **sealed**
  `transcript.v1` schema (names carried, source/confidence correct, schema-valid).

The live path (real Meet audio → real STT, eval-scored) is L4 (3.5). Covered by
`gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
