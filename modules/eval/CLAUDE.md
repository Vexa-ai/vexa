# eval — how to run a synthetic-meeting evaluation (agent guide)

You operate a controlled, ground-truthed multi-speaker meeting against the
**production** Vexa service to evaluate transcription + speaker attribution on ANY
platform. Speaker bots (production test accounts) play known TTS speeches into a
live meeting; you compare the captured transcript to ground truth and **also
eyeball it live in the extension**.

Full reference: [README.md](README.md). This file is the operating procedure.

## Preconditions
- `secrets.env` filled (`VEXA_BASE`, `PLATFORM`, `NATIVE_ID`, `TRANSCRIPTS_BASE`,
  `TOK_*`). The live values are in `~/vexa-test-rig/secrets.env`.
- A real meeting exists and its `NATIVE_ID` is set. A capture is running on it
  (the extension, or a transcription bot) whose transcript `TRANSCRIPTS_BASE`
  serves at `/transcripts/{PLATFORM}/{NATIVE_ID}`.
- Clip pools exist in `cache/` (or `EVAL_CACHE` points at `~/vexa-test-rig/cache`).
  If missing: `FORCE_REGEN=1 ./bin/eval.sh corpus` (needs `DG_KEY`).

## Decide the test (what to stress), then set the dials
The user specifies **number of speakers**, **speech-length variability**, and
**overlap variability** — map them to env (see README table):
- speakers → which `TOK_*` are set (N tokens ⇒ N speakers).
- length → `LEN_MED/LEN_SD/LEN_MIN/LEN_MAX` (set at **corpus** gen; regen to change).
- overlap → `GAP_MEAN/GAP_SD/GAP_MIN/GAP_MAX` (`gap<0` ⇒ overlapping speakers).
e.g. "4 speakers, short turns, heavy overlap" → 4 tokens · `LEN_MED=5 LEN_MAX=10`
(regen corpus) · `GAP_MEAN=-0.5`.

## The loop
1. **Launch** — `./bin/eval.sh launch`. Sends the bots in 10 s apart (IP safety) and
   waits for admission. If some don't auto-admit, admit them in the meeting UI.
2. **Drive** — `GAP_MEAN=… DURATION_S=… ./bin/eval.sh drive`. Bots speak the
   timeline; ground truth lands in `truth.jsonl`. Let it run ≥2 min.
3. **Eyeball** — watch the live transcript in the extension while it runs: are
   speakers separated, named, complete? Note obvious failures (mis-attribution,
   dropped turns, leakage).
4. **Judge** — `./bin/eval.sh judge`. Reports completeness / leakage / attribution
   vs ground truth. Read `truth.jsonl` (who spoke when) alongside the transcript to
   reason about WHERE it failed (e.g. leakage spikes under high overlap).

## Reporting
Give the three metrics + the dials used + concrete failure examples (segment text,
wrong label, true speaker from content). Compare runs by varying ONE dial at a time
(e.g. overlap sweep `GAP_MEAN` +1.5 → 0 → −0.5 → −1.0).

## Invariants
- **Production test accounts only**, staggered launches — never burst joins.
- Fixtures, `truth.jsonl`, `secrets.env` stay **out of the repo** (git-ignored);
  real transcripts are sensitive.
- A speaker never overlaps itself — every overlap is two different people.
