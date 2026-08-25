# namer-corpus — a naming change is proven against the corpus, not against one meeting

A speaker-naming fix that is validated on the meeting that motivated it has been validated
against the one sample guaranteed to agree with it. This harness replays **every Teams signal
tape reachable to the workspace** through two builds of `TrackNamer` and scores both against
ground truth established independently of either.

It exists because of meeting 26424, where a name was accepted at +35 s on 3.8 s of evidence and
half a meeting printed under the wrong human's name (Vexa-ai/vexa#1221). The fix for that
(Vexa-ai/vexa#1228) is correct on 26424 by construction. The question this harness answers is
the other one: **what does it do to the twenty-four tapes it was not written for?**

Result of the first run: [`SCORECARD.md`](SCORECARD.md).

## Why the namer replays without audio, and why that matters

`TrackNamer` consumes four inputs — RTP **csrc** edges, Teams DOM speaking **hints**, **roster**
sightings, and audio **ticks** (timestamps only, for its clock). It never sees a sample of PCM
and never sees a transcript. So a tape replays as pure metadata: `normalize.py` strips the audio
before anything reaches the replay, and 25 tapes then replay in seconds with no STT, no GPU, and
no meeting content on disk. A corpus-wide A/B is affordable precisely because the component
under test is this narrow — which is the argument for testing it at this seam rather than
end-to-end.

## The pieces

| file | what it does |
|---|---|
| `normalize.py` | one tape (local dir, or streamed from object storage) → `csrc.jsonl` · `hints.jsonl` · `roster.jsonl` · `ticks.jsonl` · `meta.json`. Drops PCM. |
| `truth.py` | per-track ground truth by hint × csrc exclusive correlation, with purity/bijection thresholds and a stated reason for every track it cannot truth |
| `replay.ts` | drives ONE `TrackNamer` (injected by path) over one tape; emits final name, time-to-first-name, time-to-final-name, and the label event stream |
| `run.sh` | both arms + the settled-window control over the whole corpus |
| `score.py` | joins the arms against truth → `SCORECARD.md` |
| `truth/*.json` | the established ground truth, pseudonymized, committed |
| `notes.json` | per-row explanations that a table cannot carry |

## Establishing truth without a human who was in the room

A tape enters the scorecard only with a per-track truth established by a method **the namer does
not use**. Three lines are available; the first is primary and the others corroborate.

1. **Hint × csrc exclusive correlation.** The DOM hint says *who* is speaking; the csrc edge says
   *which transport track* is carrying audio. A hint that lands while exactly one track is active
   attributes that instant unambiguously — overlapping instants are the ambiguity, so they are
   discarded rather than modelled. Over a whole meeting this gives a per-track name histogram;
   truth requires the leading name to hold ≥ 0.80 of the track's instants **and** that name's
   instants to be ≥ 0.70 concentrated on that one track, with a global bijection across tracks.

   This is not the namer's own computation restated. The namer decides **online**, from a prefix
   of the tape, under a settle delay and a first-past-the-post exclusivity rule. This is a
   whole-meeting aggregate with a global bijection constraint, computable only after the meeting
   ends — which is exactly what lets it serve as an oracle for a decision that cannot wait.

2. **Settled-window control replay** (`CUT_MS`). Replay with the roster's discovery window cut
   away entirely. The premature-acceptance hazard has no window to fire in, so the names that
   come out are what the evidence says with the timing hazard removed.

3. **Linguistic self-incrimination** — the vocative / self-identification judge of
   Vexa-ai/vexa#1224, measured at flagged-precision 1.0 in Vexa-ai/vexa#1225. Carried per tape
   where a verdict exists.

**A track whose truth cannot be established is listed as excluded with its reason**, and is
still replayed through both arms so that a behavioural difference on it is still visible. It is
never dropped quietly — an unexplained absence from a corpus is how a corpus stops being one.

## Running it

Tapes are not in the repository: they are production and dogfooding signal tapes, and they carry
audio and real participant names. The harness takes them from wherever the operator has them.

```bash
# 1. normalize (local tape dirs, or the object-storage streaming path — see normalize.py)
python3 normalize.py "$CORPUS" spec.json

# 2. truth, per tape. The real-name map goes OUTSIDE the repository.
for t in "$CORPUS"/*/; do
  python3 truth.py "$t" --out truth/"$(basename "$t")".json --map-out "$MAPS"/"$(basename "$t")".json
done

# 3. both arms. The two namers are the same file at two commits.
git show <base>:core/meetings/modules/mixed-pipeline/src/track-namer.ts > /tmp/namer.base.ts
git show <head>:core/meetings/modules/mixed-pipeline/src/track-namer.ts > /tmp/namer.head.ts
CORPUS="$CORPUS" OUT=/tmp/replay BASELINE_NAMER=/tmp/namer.base.ts FIX_NAMER=/tmp/namer.head.ts ./run.sh

# 4. score
python3 score.py --replay /tmp/replay --truth truth --map "$MAPS" \
  --out-json /tmp/scorecard.json --out-md SCORECARD.md
```

Injecting the namer by path rather than checking out two trees is deliberate: one driver, one
set of inputs, one difference. Two checkouts would let the arms diverge for reasons that have
nothing to do with the change under test.

## What is committed and what is not

Committed: the scripts, the pseudonymized ground truth, the notes, the scorecard. Every
participant name is replaced by a stable per-tape `P1`, `P2`, …, including inside the exclusion
reason strings.

Not committed, ever: the tapes, the normalized namer inputs (they carry real display names), the
transcripts, and the real-name maps. `--map-out` writes the map to an operator-chosen path.

Production tapes are read-only and are streamed and reduced **inside the cluster**, so audio
never leaves it — only the derived namer inputs come out.

## Reading the scorecard

- **`wrong` is never summed with `unnamed`.** A track published as `Speaker A` tells the reader
  nothing; a track published under the wrong human's name tells them something false. Only the
  second one misleads, and the corpus counts it separately.
- **A regression is a stop condition**, not a line item: baseline `correct` → fix `wrong` or
  `unnamed` prints at the top of the scorecard before any total.
- **Latency is compared only where both arms named the track.** Treating "one arm never named it"
  as infinite latency would let a correctness change turn up as a latency change.
- **Label churn is priced.** A fix that buys correctness with repaints has bought it with
  something, and the scorecard says how much.
