# meetings/eval/src

Deployment-agnostic, zero-npm-dep (ESM + Python, global `fetch`):
- [`speakers.mjs`](speakers.mjs) — the 9-voice roster + API helpers (`activeKeys` polls `GET /bots`).
- [`launch.mjs`](launch.mjs) — `POST /bots` per test account, staggered; waits for admission.
- [`drive.mjs`](drive.mjs) — the rotation/overlap engine: `POST …/speak` cached TTS on a master clock → `truth.jsonl`.
- [`corpus.mjs`](corpus.mjs) — (re)builds the TTS clip pools (Deepgram Aura); cached in `cache/`.
- [`local-timeline.mjs`](local-timeline.mjs) — cut equal-duration fake-microphone WAVs from the cached TTS pools on one seeded timeline; emits `truth.jsonl`, `timeline.json`, and a mechanical `verification.json` (including launch-stagger-adjusted gaps). Example: `OUT=/tmp/speakers SPEAKERS=A,B TURNS=14 GAP_SEC=4 LEADIN_SEC=100 TAILOUT_SEC=8 STAGGER_SEC=3 SEED=20260723 node src/local-timeline.mjs`; offline self-test: `node src/local-timeline.test.mjs`.
- [`judge.py`](judge.py) — reads `GET /transcripts/{platform}/{native}` and scores vs truth → the 3 metrics.
- [`replay.mjs`](replay.mjs) — re-send a legacy tape OR a `captured-signal.v1` (auto-detected; re-encoded to the `@vexa/capture-codec` wire) into a live desktop ingest (O-TEL-2 live twin).
- [`analyze.mjs`](analyze.mjs) — score a transcript; `--flag-issues` emits `flagged-issue.v1` records (O-TEL-3 auto-flagger, from its mis-attr / overseg oracles).
- [`flag-store.mjs`](flag-store.mjs) — the O-TEL-3 flag store + system queue + `routeToReplay` (flag→store→surface→replay-routing).

The O-TEL-2/3 eval RUNNERS (which use ajv, hoisted at the repo root) live one level up:
`../flag.test.mjs` (O-TEL-3) and `services/bot/src/replay.test.ts` (O-TEL-2 / `gate:replay`).
