# meetings/eval/src

Deployment-agnostic, zero-npm-dep (ESM + Python, global `fetch`):
- [`speakers.mjs`](speakers.mjs) — the 9-voice roster + API helpers (`activeKeys` polls `GET /bots`).
- [`launch.mjs`](launch.mjs) — `POST /bots` per test account, staggered; waits for admission.
- [`drive.mjs`](drive.mjs) — the rotation/overlap engine: `POST …/speak` cached TTS on a master clock → `truth.jsonl`.
- [`corpus.mjs`](corpus.mjs) — (re)builds the TTS clip pools (Deepgram Aura); cached in `cache/`.
- [`local-timeline.mjs`](local-timeline.mjs) — cut equal-duration fake-microphone WAVs from the cached TTS pools on one seeded timeline; emits `truth.jsonl`, `timeline.json`, and a mechanical `verification.json` (including launch-stagger-adjusted gaps). Example: `OUT=/tmp/speakers SPEAKERS=A,B TURNS=14 GAP_SEC=4 LEADIN_SEC=100 TAILOUT_SEC=8 STAGGER_SEC=3 SEED=20260723 node src/local-timeline.mjs`; offline self-test: `node src/local-timeline.test.mjs`.
- [`producer-dom-trace-station.mjs`](producer-dom-trace-station.mjs) — local
  ready-for-human Teams/Zoom DOM producer capture station. It does not launch,
  join, authenticate, or admit a meeting. Run `node
  src/producer-dom-trace-station.mjs --help`, paste its generated collector into
  the DevTools console of an already human-admitted web meeting, exercise one
  transition, then copy the collector's export and pass it through `sanitize`.
  Raw names and DOM identifiers are pseudonymized inside the page before the
  NDJSON crosses to the host. The output is already the platform replay
  dialect: Teams `tile-state` rows use only `SPEAKER_A/B/C` or `UNRESOLVED`;
  Zoom preserves every 250 ms canonical view/footer poll and uses only
  `speaker-a/b/c`. The host revalidates the exact platform keys, enums, cadence,
  and bounds, while preserving `provenance:"captured"`. Offline self-test: `node
  src/producer-dom-trace-station.test.mjs`.

- [`judge.py`](judge.py) — reads `GET /transcripts/{platform}/{native}` and scores vs truth → the 3 metrics.
- [`replay.mjs`](replay.mjs) — re-send a legacy tape OR a `captured-signal.v1` (auto-detected; re-encoded to the `@vexa/capture-codec` wire) into a live desktop ingest (O-TEL-2 live twin).
- [`analyze.mjs`](analyze.mjs) — score a transcript; `--flag-issues` emits `flagged-issue.v1` records (O-TEL-3 auto-flagger, from its mis-attr / overseg oracles).
- [`flag-store.mjs`](flag-store.mjs) — the O-TEL-3 flag store + system queue + `routeToReplay` (flag→store→surface→replay-routing).

## 🧑 HUMAN — Teams/Zoom producer DOM trace

First run `node src/producer-dom-trace-station.mjs --help`; it prints the exact
local commands and this human-only prompt. Stop and wait after giving it:

> 🧑 Join the intended Teams or Zoom web meeting in Chrome and complete any
> admission yourself. In that admitted meeting tab, paste the generated
> collector into DevTools Console, exercise exactly the intended speaker/name
> transition, and visually attest each announced pseudonym against its attached
> authored-test tile. Then run `copy(window.__vexaProducerTrace.stop())`. Paste
> only that already-pseudonymized NDJSON into a new local file. Tell me when it
> is saved.

Only after that response should the agent run the host-side `sanitize` command.
No artifact is “captured” merely because the offline station test passes.

The O-TEL-2/3 eval RUNNERS (which use ajv, hoisted at the repo root) live one level up:
`../flag.test.mjs` (O-TEL-3) and `services/bot/src/replay.test.ts` (O-TEL-2 / `gate:replay`).
