### Added

- **The deprecated 0.10 dashboard is back in the stack as an off-by-default option** — a
  `dashboard` compose profile (`docker compose --profile dashboard up -d`, port 13001) and a
  `dashboard.enabled` helm value, both wiring the pinned external image
  `vexaai/dashboard:0.10.6.3.14` to the gateway's hosted-compat surface exactly as hosted
  production runs it. It stays deprecated and versions on its own pinned tag (never
  `global.imageTag`); it ships because it is still load-bearing — the authenticated-session
  flows are only walkable through it today — and the UI users actually see should belong to a
  release. Deletion, not porting, is the deprecation exit.
  ([#813](https://github.com/Vexa-ai/vexa/issues/813))
