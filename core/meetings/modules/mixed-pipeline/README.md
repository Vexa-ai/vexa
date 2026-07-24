# @vexa/mixed-pipeline — the mixed lane (one mixed stream)

_meetings/ · module · `mixed-capture.v1` (one mixed audio stream + platform hints) → transcript segments._

Zoom, MS Teams, the in-tab extension and bot tab-audio all deliver **one mixed
audio stream** — every speaker muddled together, no per-channel routing. So the cut
has to be **derived**: [`PyannoteSegmenter`](src/pyannote-segmenter.ts) runs
`onnx-community/pyannote-segmentation-3.0` in-process (via
[`@huggingface/transformers`](https://www.npmjs.com/package/@huggingface/transformers)
+ [`onnxruntime-node`](https://www.npmjs.com/package/onnxruntime-node)) and emits a
boundary on every speaker-set change. That boundary is the **only** cut signal —
**cut-only, no diarization, no clustering, no embeddings.** Contrast
[`gmeet-pipeline`](../gmeet-pipeline/) (separate channels, names bound at capture, no
ONNX).

Each segmentation **turn** is transcribed over the shared engine
([`buffer`](../buffer/) LocalAgreement confirm + [`whisper`](../whisper/) stt.v1,
injected). Names are **derived too**, under the producer's actual contract:
[`ClusterNameBinder`](src/cluster-name-binder.ts) handles Teams'
concurrent per-participant outline/caption hints and the current legacy Zoom
`dom-active` window path, while Jitsi's smoothed global dominant-speaker stream
binds only when transition custody is complete and globally one-to-one. Zoom is
also an exclusive-trailing producer; #797 owns conversion from its legacy
window binder to its distinct measured envelope. Jitsi heartbeats advance
liveness but never mint onset testimony. A turn with no
admissible hint publishes provisionally under its segmentation id and is
**repainted in place** (same segment ids) only when causal evidence arrives. The
host wraps the emitted segments into the bus envelopes.

## Surface
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
- `causal-stale-heartbeat.test.ts` — a Jitsi same-name heartbeat before or after
  acoustic end cannot name the turn; incomplete, multi-turn, multi-token, or
  out-of-order custody—including an end for a non-current identity—fails closed.
- `causal-watermark-lock.test.ts` — a 7 s authored PCM turn stays repairable while
  open, confirms provisionally on one stable id, then repaints that same id only
  after unique transition + signal progress; omitting final progress remains unknown,
  and a later custody contradiction repaints the same id back to provisional.

In production, `PyannoteSegmenter.create` lazy-downloads the segmentation model from
Hugging Face on first use (cached thereafter). The remaining live path (real
Zoom/Teams page audio → capture → this spine → real STT) is the bot's job.
Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
