- **The terminal requires its own signing secret (#1784).** `NEXTAUTH_SECRET` no longer has a
  default anywhere: compose requires it (`make all` mints one into `.env`), the Helm chart generates
  one and keeps it across upgrades when `secrets.nextauthSecret` is empty, and Lite mints one on first
  boot and keeps it in the `vexa-lite-state` volume. The terminal refuses to start on a value shorter
  than 32 bytes or one published in this repository. Emailed sign-in links are signed with a key of
  their own (derived from `NEXTAUTH_SECRET`, or `MAGIC_LINK_SECRET`) and live at most 60 minutes.
  **If your install ever ran with the old example value, rotate it** — see
  [One-time steps after upgrading](/deployment#one-time-steps-after-upgrading).
