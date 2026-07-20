# meetings/eval — transcription debug & evaluation pipeline

> **Read this before using the folder.** It is a self-contained harness to debug the
> meeting-transcription **mixed lane** (Zoom · Teams · YouTube) on **real** or
> **synthetic** data. Several steps need a **human at a browser** — this file marks
> each one `🧑 HUMAN` with the exact prompt to give, then **stop and wait** for them.

## Mental model
The desktop (`../services/desktop`) ingests `capture.v1` on `ws://localhost:9099`:
**binary audio frames** (`ch999` = mixed remote audio · `ch1000` = local mic) plus
**text active-speaker hints**. It diarizes (pyannote) → names (hints → binder) →
transcribes (Whisper) → `transcript.v1`, served on the gateway
`http://localhost:8056` (`/ws` live, `/transcripts/{platform}/{native}` history).

Everything here **taps, drives, records, replays, or scores that one stream.**

## Tools — `./bin/eval.sh <cmd>`
| cmd | what it does | needs |
|---|---|---|
| `observe [platform] [native]` | live-watch a session: forming→churn→confirm, warm-up, `⚠ LOST` monitor, `⚠ HIJACK` flag | desktop only |
| `replay <tape.jsonl>` | re-feed a recorded tape into the ingest **verbatim + deterministic** (`SPEED=`, `REPLAY_PLATFORM/REPLAY_NATIVE=`) | desktop only |
| `analyze <platform> <native>` | score a transcript: per-speaker, `seg_N`, ✂ mid-cuts, ⊕ dups, **✗ mis-attribution** (content self-ID ≠ label) + **⚠ hijack** (`VEXA_NOISE_NAME=`); grep-friendly `SCORE` line | desktop only |
| `capture <tape>` | **RAW-SIGNAL health (LANE-AWARE)** — mixed lane: is `ch999` minted / near-silent / stalling? gmeet lane: did **per-participant `ch0..N`** audio arrive (or only your mic ⇒ SOLO / gmeet not exercised)? Catches "flaky / no transcript" that's a sick FEED, not a pipeline bug (`CAPTURE` line; verdict `healthy`·`inconclusive`·`unhealthy`). **Run this first when a session won't transcribe.** | tape only |
| `benchmark <tape> [p] [native]` | **LOSS oracle** — re-transcribe the tape's full audio offline (same STT), diff vs live: content/in-place **recall**, ✗ **truly-lost** + ~ **misplaced** spans (`BENCH` line) | desktop + STT env |
| `noise` | one bot emits brief noise bursts — the active-speaker **flicker injector** (`NOISE_DUR_MS=`) | secrets + 🧑 |
| `drive` | make admitted synthetic bots speak a timeline (`GAP_MEAN=`, `NOISE_KEY=`) | secrets + 🧑 |
| `launch` | send synthetic bots into a meeting | secrets + 🧑 |
| `corpus` | (re)generate TTS clip pools | DG key |

`observe` / `replay` / `analyze` are **LOCAL** (no secrets — they only touch the desktop on
localhost). `benchmark` is local too but needs the **STT egress** (`TRANSCRIPTION_SERVICE_URL`
/`_TOKEN`, the same the desktop uses) to re-transcribe the tape. `launch` / `drive` / `noise`
hit the **production API** (`secrets.env`) and put bots in a **real meeting**.

> **The harness no longer hides mis-attribution or loss** (it used to: `analyze` only tallied
> labels, so a wrong label or a dropped turn passed silently). `analyze` now flags ✗ mis-attribution
> + ⚠ hijack; `benchmark` is the loss oracle (full-audio recall vs live). Both are deterministic on a
> tape — the `SCORE`/`BENCH` lines are the regression gate. Caveat: on the **synthetic** corpus a
> reused clip can mask a true loss in `benchmark`'s global recall, so `misplaced` + the live `observe`
> `⚠ LOST` remain the don't-miss signals there; on **real** meetings global-absent = truly lost is clean.

## The debug loop
1. 🧑 **Record** a real meeting (below) → a tape lands in `~/vexa-test-rig/tapes/`.
2. 🤖 `analyze <p> <native>` the live session → baseline the bug counts.
3. 🤖 `replay <tape>` then `analyze` → confirm you reproduce the bugs **offline, deterministically**.
4. 🤖 Fix the pipeline (segmenter / binder). `replay` + `analyze` again → did `midcut`/`dup`
   drop **without** new `seg_N` or merged speakers? The tape is the regression test.
5. 🤖 `replay <tape> REPLAY_PLATFORM=teams` → the same data exercises the Teams path
   (a tape is platform-agnostic: mixed audio + hints).

## The two-leg witness — quality first, then speakers

A platform is proven by two runs that answer different questions, and mixing them is how a
green run hides a dead half. **Leg 1 asks "are the words right?"** through the extension, the
path a customer actually uses. **Leg 2 asks "are the names right?"** through the bot, which is
the only thing the bot uniquely carries and the only place #852 lives. Leg 2 is SHORT — 90s is
enough to see hints crossing the bridge — because it is not measuring content.

Both legs speak the same known truth, so neither depends on an STT reference:

```bash
RIG=~/vexa-test-rig
python3 src/build_truth_wav.py --speaker A --name Anna  --out $RIG/truth   # 269s · 827 words
python3 src/build_truth_wav.py --speaker B --name Boris --out $RIG/truth   # 187s · 643 words
```

**Leg 1 — quality (extension).** Put the synthetic speakers in the room, then capture:

```bash
ZOOM_URL=<url> NAME=Anna  WAV=$RIG/truth/Anna.wav  HOLD_MS=300000 node src/zoom-speaker.mjs &
ZOOM_URL=<url> NAME=Boris WAV=$RIG/truth/Boris.wav HOLD_MS=300000 node src/zoom-speaker.mjs &
# each prints AUDIO_START=<epoch> — clip offset zero, needed below
```
> 🧑 Join the same meeting in **Chrome**, **click the Vexa toolbar icon on that tab**, then
> **Start**. That click is not optional and never will be: `chrome.tabCapture.getMediaStreamId`
> needs an activeTab grant, and only a toolbar click / context menu / keyboard command produces
> one (`clients/extension/src/background.ts:538`). `extension-loop.mjs` drives everything else,
> but it cannot produce that gesture — it waits for it and says so.

```bash
python3 src/score_truth.py --truth $RIG/truth/Anna.truth.json@<epoch> \
                           --truth $RIG/truth/Boris.truth.json@<epoch> \
                           --transcript http://localhost:8056/transcripts/zoom/<native>
```

**Leg 2 — speakers (bot).** Same meeting, same speakers still talking, 90 seconds:

```bash
curl -s -X POST localhost:18156/bots -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"platform":"zoom","native_meeting_id":"<native>","bot_name":"vexa-witness"}'
docker logs -f <bot> 2>&1 | grep -E 'bridge-crossed|NO ACTIVE SPEAKER|hint'
```
`bridge-crossed=0` **with** `NO ACTIVE SPEAKER seen in Ns` ⇒ the DOM never lit anyone (a silent
room or stale selectors). `bridge-crossed=0` **without** it ⇒ the watchers saw speakers and the
hints died crossing into node — the injection-lifetime half. That discriminator is the whole
point of the 90 seconds.

Teams runs the same two legs with `src/teams-speaker.mjs` (`TEAMS_URL=`) — the extension matches
`teams.microsoft.com` and the lane is the same mixed one.

## 🧑 HUMAN-IN-THE-LOOP — when to STOP and prompt
The agent **cannot** drive the browser, mint capture permissions, or admit bots. At
these moments, stop and give the human the prompt **verbatim**, then wait for "ok".

**Recording a real meeting** (needs the desktop running with `VEXA_RECORD_TAPE=<dir>` —
restart it if not; see Gotchas):
> 🧑 Join the meeting in **Chrome** (Zoom/Teams **web**, not the desktop app). **Click the
> Vexa toolbar icon on that tab** — this mints the tab-capture stream and is **required**
> (it's lost on every tab/extension reload). Then **Start**. Tell me when it says capturing.

Then 🤖 confirm: `ls -la ~/vexa-test-rig/tapes` (a new tape is growing) + `observe` it. When done:
> 🧑 **Stop the capture.** → the tape is flushed.

**Synthetic bots** (`launch` → `drive`/`noise`):
> 🧑 Join the meeting + **Start** (to capture), then **admit the `spk-*` bots** from the
> lobby as they appear.

## Gotchas (real failures hit here)
- **"capturing 0 stream(s)" / no transcript** → the tab-capture stream id wasn't minted.
  Fix: 🧑 click the Vexa toolbar icon **on the meeting tab** (only the toolbar click grants
  it; lost on reload). Tell from a tape: many `hints` but ~0 `audio` frames.
- **Recording needs a desktop restart** with the env. The desktop is launched
  `node dist/desktop.js` from `../services/desktop` with `TRANSCRIPTION_SERVICE_URL/_TOKEN`
  set (STT). To record, add `VEXA_RECORD_TAPE=$HOME/vexa-test-rig/tapes`. Restarting drops
  any live capture → tell the human to re-Start.
- **`ch1000` (local mic) is dead in the extension** — the mic AudioWorklet loads from a
  `blob:` URL, blocked by MV3 CSP (`../modules/gmeet-capture/src/pcm-capture.ts`). Remote
  audio (`ch999`) is unaffected. Open fix: ship the worklet as a `web_accessible_resource`.
- **Oversegmentation is NOT short turns.** A dynamic call is ~half ≤3-word turns and that's
  correct. Score only ✂ mid-cuts + ⊕ dups (`analyze`). Fixes must live in the **segmenter**
  (`../modules/mixed-pipeline`), never post-hoc text-merge — Whisper already saw broken context.

## Artifacts & layout
- **Tapes**: `~/vexa-test-rig/tapes/tape-<platform>-<native>-<iso>.jsonl` — line 1 is a
  header `{v,platform,native,language,startedAt}`, then per-frame `{t, bin, d}` (`d` is
  base64 PCM when `bin`, else the JSON event).
- **Secrets + TTS clips**: `~/vexa-test-rig/` — run synthetic stages with
  `SECRETS=~/vexa-test-rig/secrets.env EVAL_CACHE=~/vexa-test-rig/cache ./bin/eval.sh <cmd>`.
- **Transcript**: `GET http://localhost:8056/transcripts/<platform>/<native>` → `{segments:[…]}`.
- **Source**: `src/{observe,replay,analyze,drive,noise,launch,corpus,speakers}.mjs`; recorder
  lives in `../services/desktop/src/desktop.ts` (gated on `VEXA_RECORD_TAPE`).
