# meetings/eval/src

Deployment-agnostic, zero-npm-dep (ESM + Python, global `fetch`):
- [`speakers.mjs`](speakers.mjs) — the 9-voice roster + API helpers (`activeKeys` polls `GET /bots`).
- [`launch.mjs`](launch.mjs) — `POST /bots` per test account, staggered; waits for admission.
- [`drive.mjs`](drive.mjs) — the rotation/overlap engine: `POST …/speak` cached TTS on a master clock → `truth.jsonl`.
- [`corpus.mjs`](corpus.mjs) — (re)builds the TTS clip pools (Deepgram Aura); cached in `cache/`.
- [`judge.py`](judge.py) — reads `GET /transcripts/{platform}/{native}` and scores vs truth → the 3 metrics.
