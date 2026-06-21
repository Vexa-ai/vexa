# @vexa/bot — eval (the bot-local L4 harness)

Validate + stress-test the **standalone carved bot** against a **live Google Meet**, with synthetic
speaker-bots, a **live eyeball viewer**, and an **autonomous PASS/FAIL verdict**. The bot is treated as
a **module whose only difference is it's a runnable service**: one command in, one verdict out.

It reuses the shared `meetings/eval` machinery in place (speakers · launch · drive · corpus · noise ·
analyze · judge · read-redis-transcript) — nothing is forked — and adds only the bot-targeted runner,
the viewer, and the verdict/attribution glue.

## One command

```bash
make -C meetings/services/bot/eval run MEETING=rvf-kywf-pxb
# or, as a one-liner subagent:  "validate bot standalone + rvf-kywf-pxb"  (see ../RUNBOOK.md)
```

What it does: starts the **viewer** (`http://localhost:8090`) → spawns the bot on **bbb** into the Meet
→ bridges its `lifecycle.v1` + `transcript.v1` live to the viewer → (you admit `vexa-0.12-bot` once) →
drives synthetic speakers from the Vexa cloud → scores the bot's transcript vs `BASELINE.md` → prints
`VERDICT PASS|FAIL` (and the suspected upstream brick on red). Full procedure + prereqs: `**RUNBOOK.md`**.

## Pieces


| File                                      | Role                                                                                                                                                                     |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `run.sh`                                  | the orchestrator (viewer + bot-on-bbb + feed bridge → drive → score → verdict). `make run MEETING=<id>`.                                                                 |
| `viewer/server.mjs` · `viewer/index.html` | the live eyeball — a dumb SSE sink (POST `/lifecycle` `/transcript` `/verdict`) + a 1-page UI: transcript feed · lifecycle timeline · verdict banner. No deps, no build. |
| `feed.mjs`                                | bbb→viewer bridge: `docker logs -f` → `/lifecycle`; poll the `transcription_segments` redis stream (via the reused `read-redis-transcript.mjs`) → `/transcript`.         |
| `verdict.mjs`                             | the autonomous oracle: runs `analyze.mjs` (SCORE) + `judge.py` (JUDGE) vs `BASELINE.md` → one PASS/FAIL, POSTs the banner, exits 0/1.                                    |
| `attribute.mjs`                           | on red, maps the failing metric → the upstream `@vexa/*` brick + the offline `gate:replay` repro command.                                                                |
| `verify.sh`                               | offline self-test of the oracle (clean→PASS, misattr→FAIL→brick). `make verify`.                                                                                         |
| `RUNBOOK.md`                              | the full self-contained procedure the validator agent follows.                                                                                                           |
| `config.env.example`                      | bbb topology + secrets path + run knobs. `cp` → `config.env` (gitignored).                                                                                               |


## Verify offline (no meeting, no bbb)

```bash
make -C meetings/services/bot/eval verify     # gates: clean fixture → PASS, misattr fixture → FAIL → brick
PORT=8097 node viewer/server.mjs &            # then curl /lifecycle /transcript /verdict and open :8097
```

## The verdict (gmeet lane, from `meetings/eval/BASELINE.md`)

HARD: `misattr=0` · `dup=0` · `seg_N=0` (fully speaker-bound) · `leakage=0` · `hijack=0` (noise lane).
SOFT: oversegmentation `midcut/segments ≤ 10%`. `completeness`/`attribution_pct` are reported, not
hard-gated (attribution over-counts under `/speak` latency — Learning #18). `0 segments` ⇒ FAIL.

## Separating concerns (debug the right module)

A red verdict points at ONE brick (`attribute.mjs`): join → `@vexa/join`; silent → capture /
`@vexa/gmeet-capture`; no text → `@vexa/transcribe-whisper`; misattr/overseg → `@vexa/gmeet-pipeline`.
Reproduce it OFFLINE + deterministically through the real pipeline — no live Meet, no STT, no server —
with `pnpm --filter @vexa/bot run replay` (`gate:replay`, `../src/replay.test.ts`).

## Prereqs (brief — full list in RUNBOOK.md)

`ssh bbb` + `vexaai/vexa-bot:v012` present; compose stack up on bbb (redis `vexa-redis-1`, runtime
`vexa-runtime-api-1`, net `vexa_vexa`); `$SECRETS` (default `~/vexa-test-rig/secrets.env`) with
`VEXA_BASE`, `PLATFORM`, `NATIVE_ID`, prod `TOK_A..H` (decrypt from `~/dev/vexa-secrets`, never commit).