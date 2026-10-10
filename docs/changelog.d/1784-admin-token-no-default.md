- **The admin key has no published default (#1784).** `ADMIN_TOKEN` (admin-api's `ADMIN_API_TOKEN`,
  flows' `VEXA_FLOWS_ADMIN_KEY`, Helm's `secrets.adminApiToken`) shipped as `dev-admin-token` in
  Compose's `.env.example` and as `CHANGE_ME` in the chart. admin-api, meeting-api and flows now refuse
  to boot on those and on every other value this repository ever shipped for the key. `make up` mints
  one into `.env` and replaces a published one there; the chart keeps the value its Secret holds, or
  generates one, and refuses an explicit published value at render (including `CHANGE_ME` carried over
  by `helm upgrade --reuse-values`). Scripts that call the admin API need the new value; API keys
  already minted keep working. See
  [One-time steps after upgrading](/deployment#one-time-steps-after-upgrading).
- **A MeetingToken is no longer signed with the admin key (#1784).** meeting-api derives the
  MeetingToken key from `ADMIN_TOKEN` (HMAC-SHA256 under a fixed purpose label) and signs and checks
  every bot's token with that, so the credential a bot holds is never signed with the key that mints
  API keys. No new secret is needed. Tokens minted before the upgrade are refused: a bot already in a
  call when you upgrade has its callbacks and uploads refused until it is sent again, so **upgrade
  between meetings**. See [One-time steps after upgrading](/deployment#one-time-steps-after-upgrading).
