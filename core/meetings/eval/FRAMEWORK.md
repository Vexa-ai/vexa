# The transcription improvement framework (#847)

_One invariant metric set · two loops · every defect attributed to one stage by recorded
evidence · every fix red→green on a fixture harvested from a real session. This file governs
transcription/attribution hardening for ALL lanes; the per-tool READMEs defer to it._

## The layer model — why fixing MIXED fixes many platforms

```
            platform-specific (capture + attribution SOURCE)      shared core (where most defects live)
gmeet:      per-channel <audio>, glow-named at source         →   @vexa/gmeet-pipeline   (per-speaker buffers, LocalAgreement-2)
jitsi:      bot webrtc hook → one mixed stream + DOM hints  ──┐
teams:      tabCapture/bot + msteams-speakers hints          ─┼─▶ @vexa/mixed-pipeline  (pyannote cuts + cluster-name-binder + LA-3)
zoom:       tabCapture/bot + zoom-speakers hints             ─┤
youtube:    tab <video>, no hints (extension)                ─┘
                                      ↓ both lanes
            @vexa/transcribe-whisper → assembly/confirm → publish (segment_id identity) → collector/store
```

Speaker attribution is platform-specific **only at the source** (glow names / DOM hints); the
binding logic is shared. One mixed-core fix lands on jitsi + teams + zoom + youtube at once;
per-platform work reduces to (a) capture delivery, (b) hint quality.

## Six stages — every defect attributed to exactly one, by evidence

`capture → segmentation → STT → assembly/confirm → publish → store`

No fix until the stage is named by a recording, not by code reading. (Calibration day, 2026-07-20:
three code-reading hypotheses — LA-3 turn loss, despeckle width, "the release branch regressed
it" — all refuted by measurement. The same day, recordings attributed every live symptom:
capture 65% duty cycle, window-size-driven hallucination, publish identity.)

## The invariant metric set (same axes, every lane, every platform)

| axis | metric | truth source | stage it indicts |
|---|---|---|---|
| delivery | **capture duty cycle** = audio-sec ÷ wall-sec | the recorded session itself | capture |
| content | recall/precision vs **single-pass same-model reference** | same audio, same STT, one pass | streaming loss (ours) vs model ceiling (not ours) |
| attribution | words under the right speaker | channel names / hints / known-text fixtures | capture naming + binder |
| integrity | duplicates; one row per sentence | segment_id identity | publish/store |
| shape | segs/turn · dur p50 · %<1s · mid-sentence ends | continuous-speech source | segmentation |
| latency | time-to-first-draft · time-to-confirm · cadence regularity | wall clock at PRODUCTION config | assembly cadence |

## Two loops + the bridge

**External — human in the loop, rare, decisive.** `clients/extension` (gmeet · youtube · zoom ·
teams tabs) → `services/desktop` (production lane config via `BOT_SPEAKER_*`; real STT). The human
judges *feel* (latency, steadiness, readability, attribution). Every session auto-records:
`VEXA_RECORD_TAPE=<dir>` (verbatim capture.v1 ingest tape) + `VEXA_STT_TAP=1` (every STT window and
its answer). Bots record the same shape in production via `CAPTURE_SIGNAL_ENABLED` → captured-signal.v1.

**Internal — no human, constant.** tape → `src/tape-to-signal.mjs` → `captured-signal.v1` → the
REAL lanes in-process (real PyannoteSegmenter; the model loads locally in ~2s) → scored on the
metric set vs `src/single_pass_truth.py`. Harnesses: `services/bot/src/quality.test.ts` (gmeet,
known-text) · `services/bot/src/quality-mixed.test.ts` (mixed, recorded session) ·
`services/bot/src/replay*.test.ts` (structure/determinism) · `src/counting_*.py` (position truth).

**The contract:**
1. A defect is only worked against a fixture the external loop produced — no fixture, no fix.
2. A fix ships only after external re-witness — instrument green is necessary, never sufficient.
3. Every witnessed session joins the regression corpus; old defects stay red-tested forever.

## Ground-truth ladder — pay only for the truth you need

1. **Continuous speech** (a YouTube tab): free; any hole or sliver is a defect by construction.
2. **Single-pass reference** (`single_pass_truth.py`): cheap, works on any real meeting; splits
   every miss into *model ceiling* vs *our streaming loss*.
3. **Known-text fixtures** (TTS clips / counting): word-level truth AND attribution truth.
4. **Human**: the only oracle for "does it feel right".

## Anti-patterns, now rules

- A mock-STT green proves structure only; it must never be reported as content quality.
- Rebuild artifacts before taking a "before" measurement; a stale `dist/` is a moving baseline.
- Pin confounds when comparing arms (e.g. `TX_EXTRA_MS` to fix STT latency).
- Never score a store that is still flushing (db-writer immutability threshold).
- Hallucination phrase-lists treat the symptom; sub-second windows are the cause.
- Fix at the point of introduction: the segmenter that "over-segments" a gappy stream is
  faithfully reporting capture's holes.

## Current baselines (2026-07-20, the calibration sessions)

| session | duty cycle | coverage | p50 seg | filler | note |
|---|---|---|---|---|---|
| jitsi bot (youtube shared into meet.jit.si) | **65.0%** | 0.225 | 0.30s | 31% on <1s windows | capture defect; segmenter innocent |
| youtube direct (extension→desktop) | ~97% | 0.671 | 3.00s | 0% | control; 42.4s of gaps = open mixed-core question |

Fixed on this branch already: mixed draft-identity (`4c030cd8`) · gmeet close-tail (`3894680d`) ·
recorder reachability (`2053c156`). Open: mixed-core streaming loss · jitsi capture duty cycle ·
gmeet cadence (~8.15s projected time-to-text) · per-platform hints (#797) · gmeet glow pre-roll (#616).
