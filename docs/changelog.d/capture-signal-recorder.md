- **Record-always at the capture boundary: bots can now persist their raw
  `captured-signal.v1` stream for offline replay.** With `captureSignalEnabled` in the
  invocation (or `VEXA_CAPTURE_SIGNAL=1`), the bot tees every raw capture frame — per-channel
  PCM audio plus the speaker events it was captured with — into a session JSONL, and logs each
  STT round-trip beside it (`<session>.stt.jsonl`). A recorded session replays byte-for-byte
  through the exact transcription pipeline with no live meeting (`REPLAY_FIXTURE=<file> tsx
  src/replay.test.ts`), and `eval/src/distill.mjs` cuts a session down to a minimal fixture
  around a reported symptom (time window / speaker). A session stores both halves of the
  signal: the audio frames and — for Zoom/Teams/Jitsi, whose single mixed stream is named from
  active-speaker hints arriving on their own channel — those hints as `type: "hint"` records,
  so a replay reproduces who spoke, not just what was heard. Off by default; when off, the
  capture path is unchanged (a single branch). This is the diagnostic layer for zero-transcript
  and misattribution reports: the failing meeting's input becomes a deterministic red test.
- **The mixed lane (Zoom/Teams/Jitsi) now has a deterministic attribution oracle.** A recorded
  session replays through the real `@vexa/mixed-pipeline` to hint-derived speaker names, offline
  and with no model — `gate:replay` runs it alongside the gmeet harness. The committed golden is
  harvested from a real meeting (two scripted speakers, ground-truth WAVs) and distilled to the
  window spanning one turn change, so "the audio survived but who-spoke collapsed to `seg_N`" —
  the shape reported in the Teams/Zoom attribution bugs — is now a red test rather than a live
  observation.
