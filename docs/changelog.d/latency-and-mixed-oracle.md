- **The replay harness gains a mixed-lane attribution oracle and a latency instrument.** A
  recorded Zoom/Teams/Jitsi session now replays through the real mixed pipeline to deterministic,
  hint-derived speaker names (`gate:replay` covers it) — the lane where the attribution bugs live
  previously had no offline oracle at all. A separate opt-in `replay:paced` harness feeds a
  recorded session at speaking rate and reports a speech→transcript latency profile, because the
  batch harnesses structurally cannot measure latency: the pipeline's submit/confirm cadence is
  wall-clock, so batch-fed audio makes every confirmation look instantaneous. The paced number is
  an instrument and a regression comparator, not yet a validated budget.
