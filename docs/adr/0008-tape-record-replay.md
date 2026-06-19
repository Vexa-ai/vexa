# ADR 0008 — Tape record/replay: deterministic ingest capture for offline repro

**Status:** accepted · 2026-06-19 · enforces **P8**

## Context

The constitution's brick-debug discipline says *"reproduce with no live meeting before you fix"* — a
brick's own logs are a claim, not proof. But the meeting ingest is a live, non-deterministic stream
(real audio + DOM hints over a WebSocket), so a transcription/attribution bug seen in a real meeting was,
until now, not reproducible without re-running a real meeting. That makes fixes unfalsifiable and
regressions invisible.

## Decision

**Record the exact ingest stream to a replayable "tape," and make replay the deterministic repro + the
regression test.**

- **Record:** when the desktop runs with `VEXA_RECORD_TAPE=<dir>`, it writes every ingest frame to a
  tape (`tape-<platform>-<native>-<iso>.jsonl`): a header line, then per-frame `{t, bin, d}` — base64 PCM
  when binary, else the JSON event (active-speaker hint, etc.). The tape is the *complete* `capture.v1`
  input the pipeline saw.
- **Replay:** the eval harness re-feeds a tape into the ingest WS **verbatim and deterministically**
  (`SPEED=`, `REPLAY_PLATFORM=`/`REPLAY_NATIVE=`), so the same data drives the pipeline offline — and the
  same tape can exercise a *different* platform path (a tape is platform-agnostic: mixed audio + hints).
- **The tape is the spec (P8).** `analyze`/`benchmark` score a replayed run; the `SCORE`/`BENCH` lines are
  the regression gate. A fix must drop the bug counts on the tape *without* introducing new `seg_N` or
  merged speakers. Fixes live in the brick that owns the symptom (the segmenter/binder), never a post-hoc
  text merge.

## Consequences

- Pipeline bugs (oversegmentation, mis-attribution, loss) become **deterministically reproducible offline**
  with zero secrets and no live meeting — the L4 debug loop is record → analyze-live → replay → fix → re-replay.
- Tapes are local debug artifacts (under `~/vexa-test-rig/tapes/`), not committed goldens; they may contain
  meeting audio, so they live outside the repo and are git-ignored.
- The recorder is a thin, env-gated tee in `desktop.ts`; it adds no coupling to the pipeline and is off
  unless `VEXA_RECORD_TAPE` is set.
