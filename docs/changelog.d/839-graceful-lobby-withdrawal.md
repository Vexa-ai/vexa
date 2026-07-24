### Fixed

- Stopping a bot while it waits for meeting admission now cancels and closes the pending join,
  persists typed withdrawal evidence, and only then removes its worker. Missing acknowledgements
  take a distinct bounded-timeout fallback instead of appearing as graceful success. (#839)
