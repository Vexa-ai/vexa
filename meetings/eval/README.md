# meetings/eval — the L4 live+eval gate

_Governed by `docs/ARCHITECTURE.md` (validation pyramid: L1 contract → L2 unit → L3 integration → **L4 live+eval**). This is the meetings domain's L4: a real meeting, scored._

A **contract-driven, self-oracling** end-to-end validator. It doesn't touch internals —
it drives whatever Vexa the bots are pointed at through the **public service API**, and
carries its own ground truth + scoring. So it validates the **live 0.11 deployment today**
(to set a baseline) and the **0.12 stack as it lands** — same harness, same truth, same
metrics.

## How it works

Service **test-account bots** join a live meeting and speak **known TTS clips** on a dialed
timeline (length + overlap), logging ground truth; then the captured `transcript.v1` is
scored against that truth. No human admits anyone; the leakage check is content-based (a
clip literally says *"Boris here…"*), so it's robust under fuzzy overlap timing.

```
launch ─► bots join + transcribe (staggered, IP-safe)
drive  ─► bots speak the timeline  → truth.jsonl (who said what, when)
judge  ─► read transcript.v1, score vs truth → completeness · leakage · attribution
```

## The acceptance contract (what the system-under-test must expose)

The harness couples to the deployment through exactly four operations — **this is the
public surface `meetings/services/meeting-api` must satisfy** (the validator defines the API,
contract-first):

| op | endpoint | purpose |
|---|---|---|
| launch | `POST /bots` `{platform, native_meeting_id, bot_name, language, task}` | spawn a bot (→ runtime kernel) |
| admit  | `GET /bots` (X-API-Key) → `[{native_meeting_id, status}]` | admission signal (`active`) |
| drive  | `POST /bots/{platform}/{native}/speak` `{audio_base64, format, sample_rate}` | drive a bot mic |
| read   | `GET /transcripts/{platform}/{native}` → `{ segments: [transcript.v1] }` | read the transcript |

So **0.12's bot is "done" when its live scores ≥ the 0.11 baseline** — objective, not "it compiles."

## Run it

Secrets + clip pools live OUTSIDE the repo (real transcripts are sensitive):

```bash
cd meetings/eval
export SECRETS=~/vexa-test-rig/secrets.env      # VEXA_BASE, TRANSCRIPTS_BASE, NATIVE_ID, TOK_*
export EVAL_CACHE=~/vexa-test-rig/cache          # the 9 cached TTS voices (no Deepgram per run)
./bin/eval.sh launch                             # send the speaker bots in (staggered)
GAP_MEAN=-0.5 DURATION_S=180 ./bin/eval.sh drive # bots speak ~3 min; ground truth → truth.jsonl
./bin/eval.sh judge                              # the 3 metrics vs ground truth
```

Dials (all env): speakers = which `TOK_*` are set; overlap = `GAP_MEAN` (lower/negative =
more overlap); length set at corpus-gen time (`LEN_*`); `DURATION_S`, `STAGGER_S`. Vary ONE
dial at a time to compare runs.

## The three metrics (`judge`)
1. **COMPLETENESS** — was each truth turn transcribed at all (any label)?
2. **LEAKAGE** — does a segment's CONTENT self-ID a speaker ≠ its label?
3. **ATTRIBUTION** — of named segments, label == true speaker (precision + unknown%).

## Invariants
- **Service test accounts only**, staggered launches — never burst joins (egress IP safety).
- `secrets.env`, `cache/`, `truth*.jsonl` stay **out of the repo** (git-ignored).
- A speaker never overlaps itself — every overlap is two different people.
- Not a workspace package (a CLI harness, run directly) — exempt from `gate:exports`/`gate:node`.

## Telemetry · replay · bug-flag (Group 8 — O-TEL-1/2/3)

Turns a **live meeting bug into a reproducible OFFLINE test**. The raw signal is teed at the bot's
capture bridge (`services/bot/src/capture-bridge.ts`, the `TelemetrySink` port) as
[`captured-signal.v1`](../contracts/captured-signal.v1); a stored signal replays through the EXACT
pipeline; analyze.mjs auto-flags defects as [`flagged-issue.v1`](../contracts/flagged-issue.v1) that
route back to the replay.

**O-TEL-2 — deterministic replay (the `gate:replay` target).** A small golden captured-signal.v1
fixture (`replay-fixture/session.captured-signal.jsonl`) replays through the real `@vexa/gmeet-pipeline`
lane (deterministic mock STT, no model, no server) → the same transcript structure every time. The
runner lives in the bot package (where the lane resolves); the **exact command** the orchestrator
wires for `gate:replay`:

```bash
pnpm --filter @vexa/bot run replay        # from the repo root (v0.12/)
# equivalently:  cd meetings/services/bot && npx tsx src/replay.test.ts
# or via this harness:  ./bin/eval.sh replay-test
```

It asserts: same input ⇒ same output (run twice, byte-identical), the expected Alice→Bob→Alice
segmentation/attribution from the captured glow names, and transcript.v1-validity.

**`replay` now takes a captured-signal.v1 OR a legacy tape** (auto-detected) and re-sends it into a
LIVE desktop ingest (re-encoded to the `@vexa/capture-codec` wire) — the server-backed twin of the
offline gate. Watch the result with `observe`.

**O-TEL-3 — flag→store→surface→replay-routing.** `analyze.mjs --flag-issues` emits flagged-issue.v1
records (issue_type from its mis-attribution / oversegmentation oracles); `src/flag-store.mjs` is the
flag store + system queue + `routeToReplay`. The offline eval:

```bash
node eval/flag.test.mjs        # or ./bin/eval.sh flag-test  (offline, no meeting, no secrets)
FLAG_SIGNAL=<captured-signal.jsonl> ./bin/eval.sh analyze <p> <native> --flag-issues   # auto-flag a live/replayed transcript
```

## Live companion — `observe` ([`src/observe.mjs`](src/observe.mjs))

Where `launch/drive/judge` **score** a synthetic run against ground truth, the live observer
**watches** a real session's transcript *dynamics* as they stream — `forming → confirm`,
per-segment gap, the oversegmentation % (≤3-word fragments), the warm-up (time to first
confirm), and a lost-transcript monitor (`⚠ LOST` = pending shown then cleared without
confirming). No secrets, no deps (Node's built-in `WebSocket`, taps the local desktop `/ws`):

```bash
pnpm observe <platform> <native_meeting_id>            # from the repo root
./bin/eval.sh observe <platform> <native_meeting_id>   # or from here — e.g. youtube 53yPfrqbpkE
pnpm observe                                           # watch ALL sessions
```
