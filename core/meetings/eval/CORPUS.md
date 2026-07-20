# The fixture corpus — witnessed sessions that stay witnessed

_The regression half of [FRAMEWORK.md](FRAMEWORK.md). A recorded session proves nothing on its own;
what makes it a regression test is the numbers it produced at the time, stored beside it. This file
is the index — the audio is not in the repo (real speech is sensitive, sessions run to tens of MB),
so a fresh checkout finds the corpus through here._

## Where it lives

```
$VEXA_CORPUS/<platform>/<slug>/          # default ~/vexa-test-rig/fixtures
  session.captured-signal.jsonl.gz       the signal exactly as captured
  baseline.json                          every metric at promotion time — the regression contract
  manifest.json                          provenance: source file, window, commit, STT, sha256
  reference.txt                          single-pass ground truth (optional; costs STT once)
  transcript.json                        the live transcript that reference was scored against
```

## The two commands

```bash
# a witnessed session becomes an entry (a desktop tape or a bot's captured-signal session)
node src/promote-fixture.mjs <tape|session> --slug 2026-07-20-my-session --platform zoom \
  [--head-sec 600] [--reference ref.txt] [--transcript <file|url>] [--note "why this exists"]

# measure every entry now and diff against its baseline; exit 1 if anything moved
node src/score-fixture.mjs [<platform>/<slug> …] [--lane] [--update]
```

`--head-sec` cuts a tape to its first N seconds. A tape grows for as long as the desktop runs, so
the session someone actually measured is a prefix of it; the cut makes that exact window
re-derivable instead of a loose side-artifact (the youtube entry below reproduces byte-identically
from a tape twice its length).

`--update` re-baselines deliberately. Never reach for it to make a red go away — a red is the
corpus doing its job; re-baseline only when the new numbers are the ones you meant to produce.

## What the three metric blocks mean — and what they do not

| block | recomputed from | drift means |
|---|---|---|
| **delivery/shape** | the stored bytes | the fixture or a scorer changed — never the pipeline |
| **content** (recall/precision) | stored reference + stored transcript | same; both sides are pinned |
| **lane** (`--lane`) | the REAL `@vexa/mixed-pipeline` re-run over the fixture, mock STT | **our code moved** — everything else is held fixed |

Only the lane block is a code regression detector, and it detects **structure**: store rows,
duplicate identity, holes, coverage, published words. Any latency derived from it is the harness's,
not production's — cadence is `replay-paced.test.ts`. Mock STT says nothing about ASR quality and
must never be reported as content evidence.

**`coverage` is harness-relative and is NOT a product figure.** It is downstream of submission
cadence, which an unpaced replay does not reproduce, so it means something only compared against
ITSELF on the same fixture and harness. Measured both ways on `zoom/2026-07-20-live-zoom`: the live
session covered **0.808** of its span, the replay of the same audio **0.378**. Reading a lane-block
coverage as "the lane covers X% of a meeting" is a factual error — that number can only come from a
live session or a paced replay.

**Its tolerances are measured, not assumed.** A tape fixture drives the real segmenter alongside an
async pump, so the interleaving — and with it the number of resubmissions — varies. Across five
consecutive runs of identical code on the youtube entry: coverage 0.905–0.906, holes 6/6/6/6/6,
published words 2746–2750 — but STT call count 429–440. Structure is stable; call-count-derived
figures are not, and `score-fixture` tolerances them from that spread. What the corpus exists to
catch moves far further: reverting `4c030cd8` takes `storeDupes` from 0 to 11.

**`retention` is recorded only against real STT.** A mock that invents fresh tokens per call turns
every LocalAgreement resubmission of the same audio into denominator it never lost, so retention
under a mock measures resubmission overlap and calls it loss.

The lane's cut source follows the fixture: a bot session replays production's own recorded
boundaries, a desktop tape has none and gets the REAL PyannoteSegmenter (~70s for a 20-minute
session).

## Entries

### `jitsi/2026-07-20-capture-gaps` — the capture defect, 8.0 MB
A bot in `meet.jit.si` with a YouTube tab shared into it: the same continuous source as the youtube
control, delivered through the bot's webrtc-hook chain instead. Recorded at `5b13eff2`.

| | |
|---|---|
| delivery | **duty cycle 0.650** · 199 gaps totalling 115.1s · p50 0.40s · max 4.37s |
| shape | inter-cut p50 0.41s · 142 cuts under 1s · silent frames 10.2% |
| content | recall 0.858 · **precision 0.723** (whisper-1, single pass) |
| lane | 57 store rows · 0 dup texts · 239 words · coverage 0.467¹ · 5 holes >2s |

This is the red evidence for **#850**. Paired with the control it is also the framework's sharpest
discrimination: same source material, same lane, same STT — 65.0% vs 94.9% delivered, and precision
0.723 vs 0.941. The invention the low duty cycle buys (27.7% of live words absent from a single pass
over the same audio) is the sub-second-window hallucination measured as a number rather than
described.

### `youtube/2026-07-20-continuous-speech` — the control, 69 MB
One continuous speaker from a shared YouTube tab through the extension into the desktop, so every
hole is a defect by construction. Derived from a tape with `--head-sec 1195.3`. Recorded at `5b13eff2`.

| | |
|---|---|
| delivery | duty cycle 0.949 · 161 gaps totalling 61.4s · p50 0.26s · max 1.02s |
| shape | no recorded cuts (tape) — the lane run segments it live |
| content | **recall 0.905 · precision 0.941** (whisper-1, single pass) |
| lane | 279 store rows · 0 dup texts · 2746 words · coverage 0.905¹ · 6 holes >2s |

The red evidence for **#854**: 9.5% of what this same model extracts from this same audio in one
pass never reaches the transcript, and 5.9% of the transcript is not in that pass. That comparison
is built from the session's DELIVERED PCM, so it measures streaming loss relative to what capture
handed over — it is structurally blind to capture loss, and cannot be read as a total.

### `youtube/2026-07-20-witnessed-panel` — the human-witnessed reference point, 35 MB
Continuous multi-voice panel audio through the extension into the desktop at production lane config.
The maintainer watched this session live and judged **time-to-text ~6s and quality good** — the only
entry carrying a human verdict, which is what the other numbers are calibrated against.

| | |
|---|---|
| delivery | **duty cycle 1.000** · **zero** gaps over 100ms in 557s |
| content | not scorable — the desktop store had restarted, so no live transcript survived to compare |
| lane | 81 store rows · 0 dup texts · 1307 words · coverage 0.909¹ · 9 holes >2s |

Its value is the far end of the delivery curve: **100.0%** here against the jitsi bot's **65.0%** on
the same capture code. Whatever removes 35% there does not touch this source at all, which is what
makes the silence gate — not the chain — the thing to change.

### `jitsi/2026-07-20-gate-off` — the #850 green arm, 2.0 MB
The same bot capture chain as the entry above, with the silence gate off. Two synthetic speakers on
an open Jitsi host (`jitsi.dorf-post.de`, which admits unauthenticated joins) — **fully autonomous,
nobody admitted anything.** Recorded at `2c9b4b62`.

| | gate on (`capture-gaps`) | gate off (this entry) |
|---|---|---|
| duty cycle | 0.650 | **0.999** |
| gaps >100ms | 199 / 115.1s | **9 / 1.9s** |
| inter-cut p50 | 0.41s | **1.47s** |
| cuts under 1s | 142 | **10** |
| provisional words | 16.7% | **0.0%** |

The pair is the framework's cleanest red→green: same chain, one policy changed, and both predicted
knock-ons visible — the segmenter stops cutting on holes, and attribution improves without the binder
being touched, because hints finally have turns to bind to. Not a matched A/B (the red arm is
continuous speech, this one sparse clips), so read the duty cycle, not the shape deltas.

### `zoom/2026-07-20-live-zoom` — the first real platform, 16.3 MB
A human-witnessed live Zoom call captured through the extension with the silence gate off. The first
entry from a platform we actually ship, and the first carrying **real** DOM hints — 142 of them over
5 names — so the binder can be tested against a real UI rather than synthetic ones.

| | |
|---|---|
| delivery | **duty cycle 1.001 · ZERO gaps over 100ms in 269s** — #850 validated on a product surface |
| attribution | 0.0% provisional, but **2 speakers published against 5 hinted** |
| shape | live p50 1.37s (31% under 1s) · replay p50 2.06s (27% under 1s) |

Its hint stream is the finding: a **2-second poll with no `isEnd` events at all**, 80% of hints naming
one participant. The binder never learns when anyone stops, and three people are lit so rarely they
lose every max-overlap vote — which is how 5 speakers become 2 while every truth-free signal except
cardinality reads clean.

¹ harness-relative — comparable only against the same fixture's own baseline, never read as the
share of a meeting the lane covers. See the note above the entries.

## The contract

1. A defect is only worked against an entry in this corpus — no fixture, no fix.
2. An entry is added the day its session is witnessed, not the day someone needs it.
3. An entry that once caught a defect keeps catching it: `4c030cd8` (mixed draft identity) reverted
   takes `youtube/2026-07-20-continuous-speech` from 0 duplicate texts to **11** — every sentence
   stored twice — and `score-fixture --lane` fails on it.
4. A transcript and a session must be the SAME meeting — `promote-fixture` refuses a mismatched
   `native_meeting_id` and an empty store, because pairing them by hand produced two confident wrong
   conclusions in one day.
5. Repeat a run before recording it as a baseline. Both instrument defects found on 2026-07-20 —
   a replay outrunning the lane's ring, and a ratio whose denominator the mock controlled — looked
   like clean numbers in a single run and were only visible across five.
