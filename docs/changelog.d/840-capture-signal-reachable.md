### Fixed
- **The capture-signal recorder is reachable by an operator.** It shipped in 0.12.15 with the bot
  honouring `captureSignalEnabled`, but nothing ever set the field: `POST /bots` didn't know the
  option existed and no deploy surface exposed it, so in every real deployment the feature was
  dead code. A new deployment-scoped `CAPTURE_SIGNAL_ENABLED` (compose + helm, **off by default**)
  now flows knob → invocation.v1 → bot, so a failed meeting can be replayed offline instead of
  being irreproducible. Off by default on purpose: it persists raw meeting audio, a stronger
  retention claim than a transcript, so enabling it is an explicit operator decision.
