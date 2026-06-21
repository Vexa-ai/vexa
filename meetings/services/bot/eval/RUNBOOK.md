# RUNBOOK — validate the standalone carved bot on a live Google Meet

This is the **complete, self-contained** procedure the `bot-standalone-validator` agent executes. The
invoker passes nothing but the intent + a meeting id; everything needed is here.

> Invocation:  `Agent(subagent_type: "bot-standalone-validator", prompt: "validate bot standalone + <meeting_id>")`
> e.g. `validate bot standalone + rvf-kywf-pxb`

## What you are validating
The **carved v0.12 bot** (`meetings/services/bot`) is the unit under test — a stateless container that
joins ONE meeting, captures → transcribes → emits `transcript.v1` (→ redis) + `lifecycle.v1`, and dies.
You spawn it against a real Google Meet, drive **synthetic speaker-bots** that say self-identifying
lines on a known timeline (the ground truth), watch it live, and deliver an **autonomous PASS/FAIL**
scored against `meetings/eval/BASELINE.md`. The bot is just a module that happens to be runnable: in,
config; out, contract events.

## Your deliverables (in order)
1. **The eyeball URL — FIRST, immediately.** The viewer runs locally on a fixed port, so the URL is
   known before anything joins: **`http://localhost:8090`** (or `VIEWER_PORT`). Tell the human to open
   it. It shows the live transcript feed, the lifecycle timeline, and the final verdict banner.
2. **The one human step:** "admit **`vexa-0.12-bot`** in `https://meet.google.com/<id>`." The bot lands
   in the Meet's waiting room; a human must let it in (and the speaker-bots, on first run). You do NOT
   need a chat reply — admission happens in the Meet UI; you poll for `lifecycle.v1 active`.
3. **The verdict** — `VERDICT PASS|FAIL …` with the per-metric line, plus module attribution if red.

## The one command
```bash
make -C meetings/services/bot/eval run MEETING=<meeting_id>
# equivalently:  bash meetings/services/bot/eval/run.sh <meeting_id>
```
Run it **in the background** and relay its grep-able phase markers as they print:
- `EYEBALL: http://localhost:8090`  → give to the human at once.
- `ADMIT: admit 'vexa-0.12-bot' …`  → relay; the human admits in Meet.
- `[run] bot is ACTIVE ✓`           → joined; speakers start.
- `VERDICT PASS|FAIL …`             → the result (also on the viewer banner).

`run.sh` does, in order: start the local **viewer** → spawn the bot on **bbb** (`docker run
vexaai/vexa-bot:v012` on network `vexa_vexa`, STT creds pulled from `vexa-runtime-api-1`, **no**
callback URL so the console-sink lifecycle log is the source) → start **feed.mjs** (bridges `docker
logs` → `/lifecycle` and the `transcription_segments` redis stream → `/transcript`) → wait for
`active` → **launch + drive** synthetic speakers from the Vexa cloud (`./bin/eval.sh launch|drive`,
writes `truth.jsonl`) → pull the transcript from redis → **verdict.mjs** (analyze + judge vs BASELINE)
→ **attribute.mjs** on red. Artifacts: `/tmp/bot-eval-<id>.{json,flags.json,verdict.json}`.

## Prerequisites (check, don't assume)
- `ssh bbb` works and `vexaai/vexa-bot:v012` is present on bbb (the carved bot image). The compose
  stack is up there (redis `vexa-redis-1`, runtime `vexa-runtime-api-1`, network `vexa_vexa`).
- **Secrets** at `$SECRETS` (default `~/vexa-test-rig/secrets.env`) exporting `VEXA_BASE=https://api.cloud.vexa.ai`,
  `PLATFORM=google_meet`, `NATIVE_ID=<meeting_id>`, and the **prod** speaker tokens `TOK_A..TOK_H`
  (one per speaker). Decrypt from `~/dev/vexa-secrets` — **never print or commit the token values**.
  The throwaway/test-user tokens are `Invalid API key` on prod; these must be the tokens that OWN the
  speaker bots. If `$SECRETS` is missing, the run skips synthetic speakers and just waits — tell the
  human they can speak in the Meet themselves to feed the bot (a smoke test, no ground-truth scoring).
- The corpus TTS clips are cached (`~/vexa-test-rig/cache`); no Deepgram key needed per run.

## The verdict (BASELINE.md, gmeet lane)
- **HARD (any → FAIL):** `misattr=0` (content self-ID ≠ label), `dup=0`, `seg_N=0` (gmeet fully
  speaker-bound), `leakage=0`, `hijack=0` (noise lane only).
- **SOFT:** oversegmentation `midcut/segments ≤ 10%` (gmeet).
- **Reported, not hard-gated:** `completeness` and `attribution_pct` (Learning #18: attribution
  over-counts under `/speak` latency drift). `0 segments` ⇒ FAIL — the bot captured nothing.

## When it goes red — name the module, reproduce offline
`run.sh` runs `attribute.mjs` automatically. It maps the symptom to the upstream brick:
- never `active` / `failed` → **@vexa/join** (+ remote-browser).
- `active` but silent / 0 segments → **capture-bridge / @vexa/gmeet-capture**.
- `active` + audio but no text → **@vexa/transcribe-whisper**.
- misattr / seg_N>0 / midcut → **@vexa/gmeet-pipeline** (segmentation + channel-binder).
- hijack → **gmeet-channel-binder** flicker debounce.
Then reproduce OFFLINE + deterministically through the REAL gmeet pipeline (no live meeting):
`pnpm --filter @vexa/bot run replay` (the `gate:replay` target, `src/replay.test.ts`). Report the
suspected brick + that command so the right module is debugged with separated concerns.

## Report back
Return: the eyeball URL, whether the bot reached `active`, the `VERDICT` line, and — if FAIL — the
attributed brick + the offline-replay command. Keep raw evidence (the SCORE/JUDGE lines), label what
was NOT checked (e.g. recording upload, loss oracle), and don't call it "validated" beyond what the
verdict shows.
