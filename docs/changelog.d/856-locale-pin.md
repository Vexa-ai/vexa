- **Pin the bot browser's UI locale so Google Meet renders English by construction (#856).** The
  bot never told its browser what language to be, so Meet localised from `Accept-Language` or IP
  geolocation and served non-English lobbies on EU/other egress — the root cause of the
  join-button-not-found class (#846). The browser now launches with `--lang` / `--accept-lang`, a
  Playwright context `locale`, and `?hl=` on the Meet URL, all driven by a `BOT_UI_LOCALE` knob
  (default `en-US`). The resolved locale (`navigator.language`, `<html lang>`) is now logged at
  lobby time and recorded in `last_error` on failure, so it is never invisible again. The #917
  structural CTA scan is demoted to diagnostic-only (it records candidate labels and telemetry but
  never clicks), and the lobby selector lists now put exact-text entries first with the broad
  structural entry last as a locale-agnostic backstop.
