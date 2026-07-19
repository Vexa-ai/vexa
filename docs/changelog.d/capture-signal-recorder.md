- **Record-always at the capture boundary: bots can now persist their raw
  `captured-signal.v1` stream for offline replay.** With `captureSignalEnabled` in the
  invocation (or `VEXA_CAPTURE_SIGNAL=1`), the bot tees every raw capture frame — per-channel
  PCM audio plus the speaker events it was captured with — into a session JSONL, and logs each
  STT round-trip beside it (`<session>.stt.jsonl`). A recorded session replays byte-for-byte
  through the exact transcription pipeline with no live meeting (`REPLAY_FIXTURE=<file> tsx
  src/replay.test.ts`), and `eval/src/distill.mjs` cuts a session down to a minimal fixture
  around a reported symptom (time window / speaker). Off by default; when off, the capture
  path is unchanged (a single branch). This is the diagnostic layer for zero-transcript and
  misattribution reports: the failing meeting's input becomes a deterministic red test.
