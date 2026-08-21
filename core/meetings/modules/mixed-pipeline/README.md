# @vexa/mixed-pipeline — platform-specific transcription over one mixed capture

_meetings/ · module · `mixed-capture.v1` (one mixed audio stream + platform hints) → transcript segments._

Zoom, MS Teams, the in-tab extension and bot tab-audio all deliver **one mixed
audio stream** — every speaker muddled together, no pre-mix PCM per participant.

## Microsoft Teams production path

Teams also emits CSRC activity edges from the RTP mixer. The live bot selects
[`TeamsCsrcGmeetPipeline`](src/teams-csrc-gmeet-pipeline.ts), which turns those edges into
CSRC-owned virtual channels with [`TeamsCsrcChannelizer`](src/teams-csrc-channelizer.ts). The same
mixed PCM is routed only to the CSRC owners active at that moment; genuine overlap may therefore
feed more than one virtual channel. Each channel uses the shared faithful Google Meet window
([`buffer`](../buffer/) LocalAgreement, prompt feedback, full-text fallback and timeout flush), and
[`TrackNamer`](src/track-namer.ts) earns the CSRC-to-display-name binding from Teams evidence.
After confirmation, [`teams-contested-word-marker.ts`](src/teams-contested-word-marker.ts) detects
phrases proven duplicated by routed-audio overlap, matching text, and nearby Whisper word times.
The shared words are bracketed as `[words]` on both rows before publication, the unresolved-pair
count stays in pipeline telemetry, and the pipeline does not choose a winner. The rival CSRC
identity is telemetry-only and never enters the API, Dashboard, or exports.

There is **no Pyannote, diarization, clustering, embedding, or voiceprint in the Teams path**.
Pyannote remains packaged only because Zoom/Jitsi still use the legacy lane; deleting it from those
platforms is outside this Teams-only release blast radius.

## Legacy Zoom/Jitsi path

Without a proven transport-identity path, the cut is **derived**:
[`PyannoteSegmenter`](src/pyannote-segmenter.ts) runs
`onnx-community/pyannote-segmentation-3.0` in-process (via
[`@huggingface/transformers`](https://www.npmjs.com/package/@huggingface/transformers)
+ [`onnxruntime-node`](https://www.npmjs.com/package/onnxruntime-node)) and emits a
boundary on every speaker-set change. That boundary is the **only** cut signal —
**cut-only, no diarization, no clustering, no embeddings.** Contrast
[`gmeet-pipeline`](../gmeet-pipeline/) (separate channels, names bound at capture, no
ONNX).

Each legacy segmentation **turn** is transcribed over the shared engine
([`buffer`](../buffer/) LocalAgreement confirm + [`whisper`](../whisper/) stt.v1,
injected). Names are **derived too**, but cheaply:
[`ClusterNameBinder`](src/cluster-name-binder.ts) picks the max-overlap **lit hint**
over the turn span (`recordHint` — Zoom active-speaker DOM, Teams captions /
voice-outline), each lag-corrected. A turn with no overlapping hint yet publishes
provisionally under its segmentation id and is **repainted in place** (same segment
ids) when a later hint window-matches or late-box-claims it. The host wraps the
emitted segments into the bus envelopes.

## Surface
`TeamsCsrcGmeetPipeline` · `TeamsCsrcChannelizer` · `TrackNamer` ·
`ChunkedTranscriber` · `PyannoteSegmenter` · `ClusterNameBinder` · types
`ChunkedTranscriberCallbacks`, `ChunkSegment`, `BoundarySource`, `BoundaryEvent`,
`PyannoteSegmenterConfig`, `HintKind`, `HintEvent`.
Front door: [`src/index.ts`](src/index.ts).

## Verify
```bash
pnpm --filter @vexa/mixed-pipeline build
pnpm --filter @vexa/mixed-pipeline test
```
The goldens are fully **offline and model-free** — each test injects its own
segmenter (`makeSegmenter`) and a scripted/stub Whisper, so the ONNX model is never
loaded and there is no network:
- `confirm-loop.golden.test.ts` — pins the LocalAgreement-3 confirm/pending/prompt/id
  loop (the shared `@vexa/transcribe-buffer` behavior) with a scripted stub Whisper.
- `teams-contested-word-marker.test.ts` — pins the exact, symmetric bracket marker and its
  routed-overlap/word-time negative controls.
- `teams-csrc-gmeet-pipeline.test.ts` — proves the production Teams publish callback brackets the
  duplicated words on both rows without leaking rival CSRC identity, while telemetry counts the
  unresolved pair and the shared GMeet-compatible buffer remains unchanged.
- `naming.smoke.test.ts` — a hint name binds to a segmentation turn (hints-only namer).
- `claim.smoke.test.ts` — late-box claim: a turn that finalized provisionally is
  repainted to the speaker whose box lit within `CLAIM_WINDOW_MS`.
- `priority.smoke.test.ts` — a stale open hint decays so a lingering previous speaker
  can't out-vote the new one.
- `concurrency.smoke.test.ts` — overlapping/queued speakers don't erase each other.
- `flicker.smoke.test.ts` — sticky attribution: a brief flicker hint can't flip an
  already-attributed turn.
- `hint-evidence.smoke.test.ts` — weak active-speaker evidence (a brief switch that
  covers little of a long turn) leaves the turn provisional instead of stamping a
  likely-wrong name; sustained hints still bind.
- `ending-context.smoke.test.ts` — speech-end cuts send a small trailing context pad
  to STT so final words survive, while transcript timestamps stay clipped to the
  committed speech boundary.
- `short-ui-switch.smoke.test.ts` — a short isolated Zoom/Teams UI speaker switch
  right after a different speaker stays provisional rather than stamping a wrong name;
  a longer turn by the new speaker still binds.

For the legacy Zoom/Jitsi path, `PyannoteSegmenter.create` lazy-downloads the segmentation model
from Hugging Face on first use (cached thereafter). Teams never constructs it. The remaining live
path (real platform page audio → selected platform spine → real STT) is the bot's job.
Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
