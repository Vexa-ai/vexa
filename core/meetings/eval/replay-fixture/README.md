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

## `session-mixed.captured-signal.jsonl.gz` — the MIXED-lane (zoom/teams/jitsi) golden

Consumed by `services/bot/src/replay-mixed.test.ts`. Where the gmeet golden proves *segmentation*
off per-channel glow-named frames, this one proves **attribution**: the mixed lane carries one
audio stream for everybody and names it only from out-of-band speaker hints, which is where
#797 / #499 / #539 live.

Provenance: harvested from a REAL jitsi meeting through the capture-signal recorder (two scripted
speakers whose microphones were ground-truth WAVs), then distilled with `eval/src/distill.mjs` to
the ~18s window spanning one Anna→Boris turn change — 58 audio frames + 9 hint records.

**Real timing, synthetic waveform** (the same convention as the gmeet golden): every frame keeps
its harvested `ts`/`pcm_len` and every hint its harvested `t`/`name`, but the PCM is a
deterministic speech-level tone. The attribution oracle reads timing and hints, never the
waveform — verified by replaying the real-audio cut and this one to identical output
(`Anna:8.5s`, `Boris:6.3s`). That drops 1.2 MB to 27 KB gzipped. Two properties are load-bearing
if you regenerate it:

- **Amplitude ~0.1.** A low-amplitude ramp falls under the capture energy gate and the session
  replays to *zero* segments.
- **A realistic first turn.** Cut too tight (≤1s of leading audio) and the pipeline's
  short-UI-switch guard correctly rejects the opening name as a spurious tile flip, so the turn
  replays as `seg_0` — a fixture artifact, not a bug.
