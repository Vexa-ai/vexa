# @vexa/eval — synthetic-meeting evaluation harness (any platform)

Drives a controlled, **ground-truthed** multi-speaker meeting end-to-end against the
**production** Vexa service, then scores the captured transcript:

1. **corpus** — speech fixtures: a pool of TTS clips per speaker (Deepgram Aura,
   1–30 s), generated once and cached. The clip **text is the ground truth** (we
   know exactly what each bot says); every clip leads with a self-ID ("Boris here…")
   so the scorer can detect mis-attribution by content.
2. **launch** — send N speaker bots into the meeting via the production API
   (`POST /bots`), **one at a time, 10 s apart**, so the egress IP isn't flagged.
   Each bot joins as its own production **test account** (`TOK_<key>`).
3. **drive** — make the admitted bots speak the clips into the live meeting via the
   production **`POST /bots/{platform}/{native}/speak`** API, on a controlled
   timeline. You dial **how many speakers**, **speech length** (min/max/median), and
   **overlap** (min/max/mean). Ground truth (who spoke when) → `truth.jsonl`.
4. **judge** — pull the captured transcript and score it vs ground truth
   (completeness / leakage / attribution). Eyeball the same meeting live in the
   extension at the same time.

Platform-agnostic: set `PLATFORM=teams|zoom|google_meet`. (Ported from the manual
`~/vexa-test-rig`; the live token/meeting values live in its `secrets.env`.)

## Setup
```bash
cd modules/eval
cp secrets.env.example secrets.env && chmod 600 secrets.env   # fill in VEXA_BASE, NATIVE_ID, PLATFORM, TOK_*
```
Fixtures (`cache/`), `truth.jsonl`, and `secrets.env` are git-ignored — **never** in the repo.

## Run
```bash
./bin/eval.sh launch     # send the speaker bots in (staggered; waits for admission)
./bin/eval.sh drive      # in another shell: bots speak the timeline + write truth.jsonl
./bin/eval.sh judge      # after ~2 min of speech: score the live transcript vs truth
```

## The dials (env; defaults reproduce the benchmarked rig)
| dial | env | default | notes |
|---|---|---|---|
| how many / which speakers | `TOK_A…TOK_H` set | — | N tokens ⇒ N speakers (9 voices cached) |
| speech length | `LEN_MED` `LEN_SD` `LEN_MIN` `LEN_MAX` | 11 / 0.65 / 2 / 30 s | lognormal; set at **corpus** gen time |
| overlap | `GAP_MEAN` `GAP_SD` `GAP_MIN` `GAP_MAX` | +0.5 / 0.8 / −1.5 / +2.5 s | `gap<0` = two different speakers overlap |
| run length | `DURATION_S` | 900 | |
| launch stagger | `STAGGER_S` | 10 | seconds between bot joins (IP safety) |

Examples: `GAP_MEAN=-0.5 ./bin/eval.sh drive` (heavy overlap) · `LEN_MED=4 LEN_MAX=8 FORCE_REGEN=1 ./bin/eval.sh corpus` (short turns, regen).

## The three metrics (`judge`)
1. **COMPLETENESS** — was each truth turn transcribed at all (any label)? (dropped audio)
2. **LEAKAGE** — a segment's content self-IDs speaker A while it's labeled B (the
   definitive content-based correctness check under fuzzy overlap timing).
3. **ATTRIBUTION** — of named segments, label == true speaker → precision + unknown%.

## Regenerating fixtures (rare — costs Deepgram credits)
```bash
FORCE_REGEN=1 CLIPS_PER=16 ./bin/eval.sh corpus    # needs DG_KEY
```
Delete `cache/<key>.json` to regenerate one speaker. Or point `EVAL_CACHE` at an
existing pool (e.g. `~/vexa-test-rig/cache`) to reuse it with zero Deepgram calls.
