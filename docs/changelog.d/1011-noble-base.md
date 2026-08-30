- **The bot and Lite images now build on Ubuntu 24.04 (Noble) (#1011).** The meeting-bot image and
  the Vexa Lite single-container image moved from the Jammy to the Noble Playwright base — one base
  for both. Self-hosters running the agent worker also get a fix: it already exec'd its Python
  interpreter as a non-root per-subject uid, but that interpreter lives under `/root`, which is
  `0700`, so every dispatch died with `Permission denied` and respawned. `/root` is now
  traverse-only for others (`chmod o+x`); it stays unlistable and root-owned files keep their modes.
