# @vexa/gmeet-pipeline — the gmeet lane (channel-routed)

_meetings/ · module · `gmeet-capture.v1` (named per-channel audio) → `transcript.v1`._

Google Meet delivers each active speaker on a **separate channel**, so audio is
routed by channel: two people talking at once land on separate per-channel streams
and are transcribed **independently** — no muddling, onsets intact. The glow names
each channel-**turn**, bound at the turn's onset and held through overlap. Identity
is **carried** (bound at capture), never derived — no diarizer, no post-hoc namer.
Contrast [`mixed-pipeline`](../) (one mixed stream, names from hints).

Each `(channel, turn)` is its own stream over the pipeline-local `SpeakerStreamManager`
([`buffer`](../buffer/) contains its parity-locked shared successor; [`whisper`](../whisper/)
supplies stt.v1),
emitting **sealed `transcript.v1`** segments to a `TranscriptSink`. The host wraps
those into the bus envelopes.

Google Meet intentionally remains unchanged in this release. Teams is proving the shared
`GmeetCompatibleBuffer` first; after that diverse-fixture proof, a later release may replace
`src/speaker-streams.ts` with the shared import and remove the duplicate implementation.

## Surface
`createGmeetPipeline` · `SpeakerStreamManager` · `isHallucination` · `setLogger` ·
types `GmeetPipeline(Options)`, `TranscriptSegment`, `TranscriptSink`, `Source`.
Front door: [`src/index.ts`](src/index.ts).

## Verify
```bash
pnpm --filter @vexa/gmeet-pipeline build
pnpm --filter @vexa/gmeet-pipeline test
```
Three goldens:
- `hallucination-filter.test.ts` — phrase-list + structural junk drop (offline).
- `pipeline-conformance.test.ts` — the **conformance** gate: drive the pipeline with a
  **stub** Whisper and validate every emitted segment against the **sealed**
  `transcript.v1` schema (offline, deterministic).
- `pipeline-realstt.live.test.ts` — the **real-STT** gate: feed known TTS clips through
  the spine + the live transcription service, assert glow-attributed, schema-valid
  `transcript.v1`. Skips without `VEXA_TX_KEY` + `EVAL_CACHE` (same skip-where-no-backend
  pattern as the runtime docker/k8s tests; turbo passes those env through).

The remaining live path (real Meet *page* audio → capture → this spine) is the bot's job (3.3+).
Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.

## Capture timestamps

Timestamped frames anchor a channel window to capture time. Confirming text advances
that window by the duration of audio removed, independent of STT response latency.
The timestamped-input discontinuity guard starts a fresh window after a gap over
two seconds; missing capture audio is not reconstructed by timestamp correction.
A feeder whose frames carry capture-callback stamps (Zoom per-track capture) sets
`callbackStampedFrames`: the guard then measures the gap between consecutive frames, because
late or lost callbacks space those stamps wider than the audio they carry. Its turn close also
finalizes only a transcript that covers every buffered sample, and otherwise submits the whole
window as the final one. Its segment and window times come from each frame's own stamp,
so a turn whose frames cover only part of its wall time keeps its timeline; at the 30 s cap the
window up to its quietest late frame is finalized from its own request and the rest stays buffered.
