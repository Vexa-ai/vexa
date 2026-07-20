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

Measured on identical 3-speaker known-text material, the two lanes price attribution very
differently: **gmeet 1.000** (named at capture, nothing to get wrong) against **mixed 0.964** with a
clean hint stream — and **0.478** when the hint stream names the wrong person a third of the time.
The mixed lane's attribution is only ever as good as its hints.

Speaker attribution is platform-specific **only at the source** (glow names / DOM hints); the
binding logic is shared. One mixed-core fix lands on jitsi + teams + zoom + youtube at once;
per-platform work reduces to (a) capture delivery, (b) hint quality.

## Speaker attribution — one engine, four thin adapters

```
zoom-speakers.ts    ─┐ 'dom-active'
jitsi-speakers.ts   ─┤ 'dom-active'   ─▶ ClusterNameBinder ─▶ ChunkedTranscriber
msteams-speakers.ts ─┘ 'dom-outline'     (THE binding logic:   (turns · publish · repaint)
                                          vote · claim window ·
gmeet-speakers.ts   ──── per-channel glow  blocked names)
                         (gmeet lane: bound at CAPTURE, no binder)
```

`ClusterNameBinder` converges two unreliable signals: the diarizer says WHEN the speaker changed
(provisional cluster ids), the platform UI says WHO around a wall-clock time. Per-platform variation
is only the hint stream (`hintKindForPlatform`: teams → `dom-outline`, else `dom-active`) and the DOM
read. Each adapter is THE shared implementation for its platform, used by BOTH the bot and the
extension. **Therefore validating the binder on ONE mixed platform validates it for all of them**;
what stays platform-specific is hint quality (selector rot, lag, null gaps).

### Its own stage chain

`name source → hint transport → binding → repaint/rename → store`

Worked examples: #616 = name source (glow re-lights behind onset) · #797 = transport/binding (hints
detected, silently discarded) · `seg_N` on screen = binding never resolved · one sentence under two
speakers = repaint.

### Four signals that need NO ground truth

1. **provisional rate** — share of published words whose `source` is `provisional-cluster-id`
   (transcript.v1 carries `source` + `confidence`, so "never bound" is machine-detectable).
2. **hint miss rate** — `onHintOutcome({outcome:'matched'|'missed'})` already exists in
   ChunkedTranscriber; #797's "discards SILENTLY" is exactly this counter going unread.
3. **rename churn / convergence** — a turn whose name flips A→B→A is defective regardless of
   which name is right.
4. **speaker cardinality** — distinct published speakers vs distinct hint names.

### Two truth-bearing oracles

5. **Self-identifying speech** — the TTS clips lead with their own name ("Boris here, …"), so the
   CONTENT carries the label: locate the self-ID in the single-pass reference, check which speaker
   the pipeline attached there. Attribution truth with no labelling and no diarization reference.
6. **Recorded hints as the binder's reference** — replay the hint stream; the binder must reproduce
   the same attribution deterministically (`replay-mixed.test.ts`).

**The split that keeps attribution off a human's time:** *binder correctness* = published vs
recorded hints (deterministic); *hint quality* = recorded hints vs reality (needs self-ID or human).

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
| attribution | provisional rate · hint miss rate · rename churn · cardinality — live on the mixed lane (#849); self-ID match (truth-bearing) still to build | channel names / hints / self-identifying fixtures | capture naming + binder |
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
- **A ratio whose denominator the instrument controls is not a measurement.** `retention` under a
  mock that invents fresh tokens per call counts every LocalAgreement resubmission as new words
  lost. The numerator was stable across five runs (2746–2750) while the denominator moved
  (3935–4138) — that spread is the tell, and it is only visible if runs are repeated.
- **Repeat a run before recording it as a baseline.** Nothing above was detectable from a single
  run; every one of them looked like a clean number.
- **A replay must not outrun the code it replays.** Feeding a synchronous ring in a tight loop fed a
  whole session before the async pump ran once, so the audio was evicted before anything read it and
  the run scored the leftovers. The tell was a submission p50 of 0.25s against production's ~2s:
  when an instrument's cadence does not resemble production's, the instrument is the thing to doubt.
- Rebuild artifacts before taking a "before" measurement; a stale `dist/` is a moving baseline.
- Pin confounds when comparing arms (e.g. `TX_EXTRA_MS` to fix STT latency).
- Never score a store that is still flushing (db-writer immutability threshold).
- Hallucination phrase-lists treat the symptom; sub-second windows are the cause.
- Fix at the point of introduction: the segmenter that "over-segments" a gappy stream is
  faithfully reporting capture's holes.

## Current baselines (2026-07-20, the calibration sessions)

Both are corpus entries now — the numbers below are `baseline.json`, not prose ([CORPUS.md](CORPUS.md)).

| entry | duty cycle | recall | precision | note |
|---|---|---|---|---|
| `jitsi/2026-07-20-capture-gaps` | **0.650** | 0.858 | **0.723** | capture defect (#850); the invention is the sub-second-window hallucination, measured |
| `youtube/2026-07-20-continuous-speech` | 0.949 | **0.905** | **0.941** | control; the 9.5% streaming loss is #854 |

Same source material, same lane, same STT on both — so the gap between the rows is the bot's capture
chain and nothing else.

Fixed on this branch already: mixed draft-identity (`4c030cd8`) · gmeet close-tail (`3894680d`) ·
recorder reachability (`2053c156`). Open: mixed-core streaming loss · jitsi capture duty cycle ·
gmeet cadence (~8.15s projected time-to-text) · per-platform hints (#797) · gmeet glow pre-roll (#616).

## Coverage matrix — what has actually been measured (fill in; blanks are honest)

| platform | lane | delivery | content | attribution | integrity | shape | latency |
|---|---|---|---|---|---|---|---|
| youtube (extension) | mixed | ✅ 94.9% | ✅ recall .905 / prec .941 | n/a — no hint source | ✅ 282 rows / 0 dupes | ✅ p50 3.0s | ❌ |
| jitsi (bot) | mixed | ✅ 65.0% | ✅ recall .858 / prec .723 | ✅ 16.7% provisional · 91.9% hint miss | ✅ 57 rows / 0 dupes | ✅ p50 0.30s | ❌ |
| zoom | mixed | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| teams | mixed | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| gmeet | gmeet | ❌ (own capture path, NOT #850's chain) | ✅ recall .873 / prec .963 (known text) | ✅ **1.000** (14/14, named at capture) | ❌ | ✅ 1.6 segs/turn | ✅ ~8.15s projected |

## Known gaps in THIS framework (audited 2026-07-20)

Written down because an un-named gap becomes a false claim of coverage — the exact failure that
made 0.12.16 aim wrong.

**G1a — DEMONSTRATED, and worse than stated.** The four truth-free signals are blind to
MISATTRIBUTION, not merely to content. On a synthetic 3-speaker fixture whose hint stream names the
wrong person 34% of the time, attribution accuracy collapses to 47.8% — 12 of 23 rows under the
wrong speaker — while every truth-free signal reads healthy: 0.0% provisional, 3 speakers published
against 3 hinted, nothing unnamed. A lane that confidently names every turn after the wrong person
scores perfectly on all of them. Truth-free signals answer "did the namer run", never "was it right".

**G1 — the content oracle is speaker-blind.** `single_pass_truth.py` scores recall/precision on a
flat word sequence. A transcript with perfect words and completely scrambled speakers scores
1.000/1.000. This is mock-blindness repeating on a different axis. Until the self-ID scorer exists,
content green must never be reported as attribution evidence.

**G2 — CLOSED (#849).** The four truth-free signals are implemented in the mixed lane's harness and
ride in every corpus entry's lane block: `provisionalRate`, `hintMissRate`, `renames`/`churnedTurns`,
and published-vs-hinted cardinality. Still open and tracked separately: the gmeet lane binds at
capture from the glow and needs its own reading (#851/#853), and no self-ID oracle exists yet — these
score WHETHER a name was assigned, never whether it was RIGHT.

**G3 — the reference is uncalibrated.** Nothing verifies the single-pass reference is better than
the live transcript; recall may be measuring agreement, not truth. Needs: a second-pass stability
check, and one run against a known-text fixture where absolute truth exists. Chunking is also
unvalidated — 60s windows with 3s overlap can both duplicate at seams (inflating the reference) and
drop words at them.

**G4 — CLOSED (#848).** The corpus exists: `$VEXA_CORPUS/<platform>/<slug>/` with a session, a
`baseline.json` recording every metric at promotion time, a `manifest.json` pinning provenance, and
an index in [CORPUS.md](CORPUS.md). `src/promote-fixture.mjs` makes an entry, `src/score-fixture.mjs`
re-measures one and fails on drift. Both calibration sessions are entries.

**G5 — nothing runs in CI.** None of the instruments are in `scripts/gates.mjs`. Every metric here
is a thing a human remembers to run, so regressions are caught only by luck.

**G6 — partly closed (#848).** `quality-mixed.test.ts` now models the consumer: every published
segment AND every pending tail upserted by `segment_id`, last write wins, reported as `storeRows` /
`storeDupes`. That is the metric publish-side numbers could not give — and building it caught the
instrument's own blindness, because the first version dropped the `pending` argument production
actually ships and therefore could not see a draft-identity defect at all. Still open: the db-writer's
own flush thresholds (the stage the false "64.9% dropped" came from) are modelled by a *rule*
("never score mid-flush"), not measured.

**G11 — no per-lane loss parity.** Content loss was measured on the MIXED lane (recall .905) and
never on gmeet against a real-audio reference. The lanes have different confirm economies
(LA-2 vs LA-3), different capture paths (per-channel elements vs the bot's webrtc-hook chain) and
gmeet-only loss surfaces (glow gating #616, the silence gate). A measurement on one lane says
nothing about the other; the coverage matrix must be filled per lane, not per pipeline.

**G7 — latency is not measured in the live loops.** `replay-paced` measures it offline at production
config; the external loop only asks a human whether it "feels" fast. Nothing records time-to-first-
text live, so the number a user actually experiences is unlogged.

**G8 — English only.** Every measurement here is English. The originating complaint was a Russian
meeting, and hallucination boilerplate is language-conditioned (`Продолжение следует` vs
`Thank you.` vs `ご視聴ありがとうございました`). No non-English fixture, no per-language baseline.

**G9 — no overlap metric.** Real meetings have simultaneous speech; the segmenter emits
overlap-onset/offset and nothing scores whether overlapped turns survive attribution.

**G10 — no single session bundle.** Input (tape), intermediates (segmenter cuts, STT tap) and output
(published segments) are three artifacts in three places; correlating them is manual. The bot's
`captured-signal.v1` carries cuts, the desktop tape does not.
