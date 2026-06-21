# replay-fixture — O-TEL-2/3 deterministic offline fixtures

Small, committed fixtures for the offline replay + flag evals (no meeting, no model, no server):

- `session.captured-signal.jsonl` — a golden [`captured-signal.v1`](../../contracts/captured-signal.v1)
  session (header + 36 frames, Alice→Bob→Alice on gmeet channels). Replayed by
  `services/bot/src/replay.test.ts` (the `gate:replay` target) through the EXACT gmeet pipeline; the
  base64 PCM is the `@vexa/capture-codec` wire payload so it round-trips bit-exactly.
- `transcript-misattr.json` — a transcript with a PLANTED mis-attribution (a `spk-anna` segment whose
  content self-IDs "Boris"), fed to `analyze.mjs --flag-issues` to prove the O-TEL-3 auto-flagger
  emits a conforming `flagged-issue.v1`.

Deterministic: re-running yields identical output. The fixtures are intentionally tiny — they test
that the pipeline produces the SAME segmentation/structure for the same raw signal, not STT quality.
