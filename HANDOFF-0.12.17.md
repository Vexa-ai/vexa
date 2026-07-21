# Handoff → 0.12.17

_What 0.12.17 inherits, and the ground it starts from. Written at the close of the 0.12.16
attribution/quality work on `release/0.12.16` (`~/vexa-01216`). The bot runs **from source**
(`npm run build` → `node dist/index.js`); the compiled dist carries fixes no published image has._

---

## TL;DR
0.12.16 root-caused and fixed Zoom bot attribution (#852) live, proved the #850 capture fix from the
bot, and built a meeting-free quality oracle. **0.12.17's headline is the defect that surfaced
underneath #852: the mixed-lane binder drops speaker names it already has** — shared across Zoom,
Teams and YouTube, fixable entirely offline. Teams has still never been instrumented, and Zoom name
*accuracy* is still unmeasured. Deploy-to-staging remains unexecuted (owner's call).

---

## 0.12.17 owns

### 1. Mixed-lane binder drops available names  ← headline, offline, deterministic
Replaying the fixed live Zoom capture (`botsig9`) through the lane: **27 of 66 turns (30.7% of words)
publish as `seg_N` instead of a name.** Verified (self + workflow `wf_b4df30ca-05e`):
- **`seg_N` is a per-TURN counter** (`chunked-transcriber.ts:215,294,442`), not a cluster id — this
  lane does **no** speaker clustering (pyannote is a cut signal only). "26 distinct seg_N" ⇒ "26
  unnamed turns," nothing about speaker count. *(A "26 phantom speakers" reading is retracted.)*
- **The name was available and dropped:** 27/27 unnamed turns overlapped a live DOM hint, continuous
  coverage, 23/27 with a single unambiguous name. Not signal absence — a binder gate rejected it.

Candidate gates in `cluster-name-binder.ts` `windowMatch`: flicker debounce (turns <1000ms score 0),
support ≥450ms, coverage ≥0.35, confidence ≥0.6. Confidence/contested is **not** the main cause
(23/27 unambiguous). Structural gap: **repaint fires only on an incoming hint, no timer sweep** — a
name that arrived just before hints paused, sub-gate, is never revisited.

**Owner: `@vexa/mixed-pipeline`. Shared with Teams and YouTube — not Zoom-specific** (Meet uses a
separate binder, unaffected). **Next step:** instrument the binder's per-turn reject reason, replay
`scratchpad/botsig9/89237402037.captured-signal.jsonl` through `quality-mixed.test.ts`, count which
gate fires, fix at the point of introduction. No meeting required.

### 2. Teams: never instrumented
Its DOM may differ from the extension's the way Zoom's did (bot browser has no GPU/camera → Zoom
served a single-tile layout with different classes). **Screenshot first** — the tooling is in place
(`VEXA_DEBUG_SHOT_DIR`, `teams-speaker.mjs`). Needs a Teams meeting URL.

### 3. Zoom name ACCURACY still unmeasured
The always-on rooms are listening-only, so only "names assigned" is proven, not "names correct." The
known-truth oracle (`score_truth.py`) needs speaking bots in a room with known TTS. Until then, Zoom
attribution correctness is unverified.

### 4. Deploy to staging cluster — unexecuted, owner's call
Standing goal: staging → debug on staging → claim prod. Only local validation is done.

---

## Baseline 0.12.17 starts from (proven in 0.12.16, live from the bot)

- **#852 fixed** (`19d84597`): Zoom served the bot a single-tile layout; added selector
  `.single-main-container__video-frame`. Same room, before→after: `NO ACTIVE SPEAKER` ×3→**0**,
  `bridge-crossed` 0→**105**, **4 real speakers** named to published rows. *Found by a screenshot —
  every DOM probe read zero; the picture showed the name on screen.*
- **#850 holds live on Zoom from the bot** (`botsig4`, 8 min): duty **100.0%**, one 0.1s gap,
  `processor deficit 0.0s`. Previously only extension-proven.
- **Three observability silences fixed** (`fdb52a0a`): empty `.catch` on page-bundle inject; console
  forwarder that filtered out `speaker`/`hint`; over-claiming blindness reporter.
- **Meeting-free quality oracle** (`a6f9f8ae`, `9d9a1cee`, `7f1144c5`): real mixed lane over known
  TTS truth → recall **0.924**, precision **0.936**, attribution **0.947**, identical across 3 runs.
  Starts at `captured-signal.v1` → says nothing about capture delivery or DOM naming (those are the
  live legs). See `core/meetings/eval/CORPUS.md` → "synthetic entry that needs no session".

## Corrections banked — do NOT re-derive
- "Zoom selectors are stale" → FALSE; per-browser layout difference.
- "Fake-camera flag reproduces it" → CONTAMINATED; that probe sat on Zoom's *"Automated bots aren't
  allowed to join"* page. Browser-args change reverted.
- Truth scorer "epoch drives attribution not content" → BACKWARDS; a clock slip billed lost words to
  the pipeline. Fixed by scoring each speaker's WAV independently.
- "26 phantom speakers / clusters not reused" → over-read of a turn counter.

## Infra / blockers
- **Docker daemon wedged** (`Docker.raw` 81 GB; host hit 0 bytes free; `docker builder prune -f` hung).
  Not blocking — bot runs from source — but image builds are stuck. Free space before any image work.
- **Zoom bot-detection firing** on this machine for standalone `zoom-*.mjs` probes (the bot itself
  still joins). Keep probe volume low.

## Repro pointers
- Bot from source: `cd core/meetings/services/bot && npm run build`, then
  `VEXA_BOT_CONFIG=<invocation.v1> VEXA_BROWSER_UTILS_PATH=$PWD/dist/browser-utils.global.js
  VEXA_CAPTURE_SIGNAL_DIR=<dir> [VEXA_DEBUG_SHOT_DIR=<dir>] node dist/index.js`. Config needs a
  `redisUrl`; acts-plane errors are non-fatal noise if redis is absent.
- Session artifacts: `scratchpad/botsig{4,9}/`, `scratchpad/zoom-live.replay.json`, `scratchpad/shots/`.

## Key commits (0.12.16 attribution/quality)
```
19d84597 fix(zoom): bot served different layout than extension (#852) ← the fix
fdb52a0a fix(bot): three silences that hid #852
7f1144c5 feat(eval): meeting-free mixed-lane quality number (#854,#852)
9d9a1cee fix(eval): truth scorer billed a clock error to the pipeline (#854)
a6f9f8ae feat(eval): score transcript vs KNOWN truth, words+speakers (#854)
5493824a fix(eval): extension loop cannot mint tab capture, says so (#854)
45333edb fix(bot): speaker watchers re-arm on navigation (#852)
```
