- **Teams CSRC: the bot now composes an 800 ms transport-sensor inactivity window (#1383).** The
  sensor's built-in 400 ms spans a packet gap, not a speech pause; the composition root now passes
  a measured 800 ms window through the sensor's own `inactiveMs` seam. Overridable per deployment
  via `VEXA_CSRC_INACTIVE_MS` (compose, helm `runtime.csrcInactiveMs`, lite; forwarded to spawned
  bots — see [Configuration](/configuration)); a non-blank unusable value (NaN, below one 100 ms
  poll, above 10 s) warns and falls back to 800 ms, and the sensor itself now refuses an unusable
  window from any caller. The fragmentation reduction (≈1.7 → ≈1.1 lane activations per turn) is a
  downstream measurement; the upstream live leg stays open on #1383.
