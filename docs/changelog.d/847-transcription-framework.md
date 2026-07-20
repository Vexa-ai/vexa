### Added
- **The transcription improvement framework (#847)** — `core/meetings/eval/FRAMEWORK.md`: one
  invariant metric set (capture duty cycle · single-pass-reference recall · attribution ·
  integrity · shape · latency), two loops (external human witness with auto-recorded tapes;
  internal fixture replay with the real segmenter), and the contract that every defect is
  attributed to one pipeline stage by recorded evidence and every fix is red→green on a fixture
  from a real session. New instruments: `eval/src/tape-to-signal.mjs` (desktop tape →
  captured-signal.v1 — the bridge between the loops), `eval/src/single_pass_truth.py`
  (same-audio same-model single-pass reference that separates our streaming loss from the model's
  ceiling), a desktop STT tap (`VEXA_STT_TAP=1`), and the desktop now runs the SHIPPED lane
  cadence (`BOT_SPEAKER_*`, production defaults) instead of a private one.

### Fixed
- extension README claimed "Google-Meet-only by design" — the code detects and captures
  gmeet, YouTube, Zoom and Teams tabs (`src/meeting.ts`); the README now states the real
  platform matrix and which lane each feeds.
