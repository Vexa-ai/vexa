- **The admin key has no published default (#1784).** `ADMIN_TOKEN` (admin-api's `ADMIN_API_TOKEN`,
  flows' `VEXA_FLOWS_ADMIN_KEY`, Helm's `secrets.adminApiToken`) shipped as `dev-admin-token` in
  Compose's `.env.example` and as `CHANGE_ME` in the chart. admin-api, meeting-api and flows now refuse
  to boot on those and on every other value this repository ever shipped for the key. `make up` mints
  one into `.env` and replaces a published one there; the chart keeps the value its Secret holds, or
  generates one, and refuses an explicit published value at render (including `CHANGE_ME` carried over
  by `helm upgrade --reuse-values`). Scripts that call the admin API need the new value; API keys
  already minted keep working. See
  [One-time steps after upgrading](/deployment#one-time-steps-after-upgrading).
