- **Local Lite development can prove which checkout is running.** The opt-in `make lite-dev` path
  fingerprints the current source, binds it to an immutable image and app container, refuses
  source drift during the build, and exposes mismatch through `make lite-status`; ordinary
  `make lite` remains available unchanged.
