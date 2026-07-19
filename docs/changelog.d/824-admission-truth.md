### Fixed

- **A bot that never got into the meeting is no longer reported as a completed meeting.** Stopping
  a bot still sitting in the waiting room overwrote the stage it had reached, so the terminal
  classifier concluded the bot had been live and persisted the run as `completed` with zero
  transcript — a failure the system reported as success. The stage now survives the stop, and such
  a run ends `failed` with `failure_stage` naming where it actually died. Reproduced on the deployed 0.12.14 build
  (meeting 24336's own transition log records the illegal edge); the prior-era share of this
  shape was ~49% of zero-segment completions.
  ([#807](https://github.com/Vexa-ai/vexa/issues/807))
- **Cancelling a bot before it is admitted no longer re-spawns it three times.** The terminal
  reason for a user stop is now the user-terminal `stopped` (permanent) rather than
  `awaiting_admission_timeout` (transient), so a meeting the user deliberately walked away from is
  not retried on their quota. An admission wait that times out on its own is unchanged and still
  retried. ([#807](https://github.com/Vexa-ai/vexa/issues/807))
- **A redelivered `DELETE /bots/…` no longer publishes a second leave command.** The stop trigger
  is one-shot, guarded on the recorded user intent instead of a status side-effect — the previous
  guard held only against the in-memory test double, never against the real database.
  ([#807](https://github.com/Vexa-ai/vexa/issues/807))
