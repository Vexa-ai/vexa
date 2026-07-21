### Fixed
- **gmeet: a speaker's words appear sooner and arrive more steadily (#851).** Two cadence defects in
  the per-speaker assembly loop are fixed. A submit tick that fired while an STT request was in flight
  used to be dropped with no catch-up, so the owed window waited a full submit interval — at
  production STT (round-trip longer than the interval) nearly every other tick dropped and the update
  rhythm beat irregularly; the dropped tick is now recorded and the submission fires the moment the
  in-flight response lands. And a turn's first window used to wait for the next tick even after enough
  audio had accrued, adding up to a full interval of dead air before any text; it now submits as soon
  as it is ready. Measured on the paced replay harness (gmeet golden, production config): pipeline
  overhead p95 drops from 3953ms to 2007ms, and at a real 2.1s STT round-trip time-to-first-text falls
  from a projected ~8.15s to a measured 6.20s (first draft 4.10s).
