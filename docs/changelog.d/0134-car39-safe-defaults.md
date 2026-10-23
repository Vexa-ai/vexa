- **The 0.12 transcript hand-off ships off.** `VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S` defaults to
  `0`, so meeting-api refuses unsigned transcript entries. An upgrade from 0.12 with meetings in
  flight sets it to `14400` for the cut-over and removes it afterwards (Helm
  `compat.legacyUnsignedWindowSeconds`). See [Upgrade every component together](/upgrade-from-0-12#upgrade-every-component-together).
- **Every open 0.12 compatibility switch is announced at start.** With `VEXA_WS_SCOPE_MODE=legacy`,
  `VEXA_WEBHOOK_STORED_URL_POLICY=v012` or the hand-off window above `0`, the gateway and meeting-api
  print a warning banner and log `compat_switch_open`. All three are removed in 0.14.0.
- **Vexa Lite asks for a sign-in.** Zero-login (`VEXA_TERMINAL_ZERO_LOGIN`) is off by default and an
  explicit opt-in. A fresh Lite prints a one-time sign-in link to its log, and
  `docker exec vexa-lite vexa-signin-link` prints a new one. See [Vexa Lite](/deployment-lite).
- **Bot-account sign-in hosts are named exactly.** `VEXA_BOT_SIGNIN_EXTRA_HOSTS` no longer accepts
  `*.suffix`; a wildcard refuses meeting-api's start and the Helm render. See [Bot accounts](/minutes/bot-accounts).
- **Helm: meeting and onboarding facts reach flows.** With `flows.enabled`, meeting-api and admin-api
  publish to the in-chart flows-api with its operator key, as under Compose.
- **Rollback from 0.13 to 0.12 rolls the services before the gateway.** The Helm rollback steps hold
  the gateway until meeting-api, admin-api and agent-api are back, so callers get no `401` from a
  0.12 gateway in front of a 0.13 service. See [Rollback](/upgrade-from-0-12#rollback).
