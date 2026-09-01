- **Teams CSRC lanes stop fragmenting on natural speech pauses (#1383).** The transport sensor's
  400 ms inactivity window spans a packet gap, not a speech pause — the median pause (p50 ≈ 550 ms)
  already tripped a synthesized deactivation, splitting one speaker turn into ~1.7 lane activations.
  The composition root now passes the measured 800 ms window (the knee: 1.13 activations/turn),
  overridable via `VEXA_CSRC_INACTIVE_MS` where the bot process env is operator-controlled; an
  unusable override (empty, NaN, below one 100 ms poll, above 10 s) warns and falls back to the
  measured default instead of silently poisoning the sensor.
