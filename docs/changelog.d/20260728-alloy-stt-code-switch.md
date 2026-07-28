- **Opt-in local multilingual STT re-detects language at natural pauses.** Alloy auto-language mode
  now submits pause-bounded chunks sequentially without a language pin and merges their original
  timestamps, while the default configured-language path remains unchanged. The Meet capture
  boundary now retains a bounded two-second silence hangover so natural code-switch pauses reach
  STT without admitting unbounded idle audio. Two fresh clean-image Google Meet witnesses produced
  recognizable EN → RU → EN with monotonic timestamps and no clipping at any measured boundary.
