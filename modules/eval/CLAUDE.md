# eval — how to run a synthetic-meeting evaluation (agent guide)

You operate a controlled, ground-truthed multi-speaker meeting to evaluate
transcription + speaker attribution on ANY platform. Speaker bots (service test
accounts) play known TTS speeches into a live meeting; you compare the captured
transcript to ground truth and **also eyeball it live in the extension**.

This drives whatever Vexa the bots are pointed at — normally the **desktop** hot
rig (see a failure → fix a brick → desktop reloads → re-run), but equally any
deployment that can launch bots. The conceptual "why" is in [README.md](README.md);
this file is the operating procedure.

## Setup (once)
```bash
cd modules/eval
cp secrets.env.example secrets.env && chmod 600 secrets.env
#   fill: VEXA_BASE        — the API that launches bots + accepts /speak
#         TRANSCRIPTS_BASE — where the captured transcript is read from
#         PLATFORM         — teams | zoom | google_meet
#         NATIVE_ID        — the meeting id
#         TOK_A…TOK_H      — bot test-account keys (one per speaker)
```
Live token/meeting values are in `~/vexa-test-rig/secrets.env`. Fixtures
(`cache/`), `truth.jsonl`, and `secrets.env` are git-ignored — **never** in the repo.

## Preconditions
- `secrets.env` filled (above).
- A real meeting exists, its `NATIVE_ID` set, and a capture is running on it (the
  extension, or a transcription bot) whose transcript `TRANSCRIPTS_BASE` serves at
  `/transcripts/{PLATFORM}/{NATIVE_ID}`.
- Clip pools exist in `cache/` (or `EVAL_CACHE` → `~/vexa-test-rig/cache`). If
  missing: `FORCE_REGEN=1 ./bin/eval.sh corpus` (needs `DG_KEY`).

## Decide the test (what to stress), then set the dials
The user specifies **number of speakers**, **speech-length variability**, and
**overlap variability** → map them to env:

| dial | env | default | notes |
|---|---|---|---|
| how many / which speakers | `TOK_A…TOK_H` set | — | N tokens ⇒ N speakers (9 voices cached) |
| speech length | `LEN_MED` `LEN_SD` `LEN_MIN` `LEN_MAX` | 11 / 0.65 / 2 / 30 s | lognormal; set at **corpus** gen time |
| overlap | `GAP_MEAN` `GAP_SD` `GAP_MIN` `GAP_MAX` | +0.5 / 0.8 / −1.5 / +2.5 s | `gap<0` ⇒ two different speakers overlap |
| run length | `DURATION_S` | 900 | |
| launch stagger | `STAGGER_S` | 10 | seconds between bot joins (IP safety) |

e.g. "4 speakers, short turns, heavy overlap" → 4 tokens · `LEN_MED=5 LEN_MAX=10`
(regen corpus) · `GAP_MEAN=-0.5`.

## The loop
1. **Launch** — `./bin/eval.sh launch`. Sends the bots in `STAGGER_S` apart (IP
   safety) and waits for admission. If some don't auto-admit, admit them in the UI.
2. **Drive** — `GAP_MEAN=… DURATION_S=… ./bin/eval.sh drive`. Bots speak the
   timeline; ground truth lands in `truth.jsonl`. Let it run ≥2 min.
3. **Eyeball** — watch the live transcript in the extension while it runs: are
   speakers separated, named, complete? Note obvious failures (mis-attribution,
   dropped turns, leakage).
4. **Judge** — `./bin/eval.sh judge`. Reports the three metrics vs ground truth.
   Read `truth.jsonl` (who spoke when) alongside the transcript to reason about
   WHERE it failed (e.g. leakage spikes under high overlap).

## The three metrics (`judge`)
1. **COMPLETENESS** — was each truth turn transcribed at all (any label)? (dropped audio)
2. **LEAKAGE** — a segment's content self-IDs speaker A while it is labeled B (the
   definitive content-based correctness check under fuzzy overlap timing).
3. **ATTRIBUTION** — of named segments, label == true speaker → precision + unknown%.

## Reporting
Give the three metrics + the dials used + concrete failure examples (segment text,
wrong label, true speaker from content). Compare runs by varying ONE dial at a time
(e.g. overlap sweep `GAP_MEAN` +1.5 → 0 → −0.5 → −1.0).

## Regenerating fixtures (rare — costs Deepgram credits)
```bash
FORCE_REGEN=1 CLIPS_PER=16 ./bin/eval.sh corpus    # needs DG_KEY
```
Delete `cache/<key>.json` to regen one speaker, or point `EVAL_CACHE` at an
existing pool (`~/vexa-test-rig/cache`) to reuse it with zero Deepgram calls.

## Invariants
- **Service test accounts only**, staggered launches — never burst joins.
- Fixtures, `truth.jsonl`, `secrets.env` stay **out of the repo** (git-ignored);
  real transcripts are sensitive.
- A speaker never overlaps itself — every overlap is two different people.
