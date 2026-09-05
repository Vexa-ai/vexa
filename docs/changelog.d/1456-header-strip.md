- **The gateway strips authority headers by family, not by name (#1456).** A public client could
  previously send `x-internal-secret` — the value shipped in `docker-compose.yml` — and be believed
  by the internal tier, because the strip was an eight-name list of `x-user-*` spellings. Any header
  beginning `x-user-`, `x-internal-` or `x-vexa-internal-`, plus `x-admin-api-key` and
  `x-gateway-verified`, is now dropped from every client request. **Upgrade the gateway with, or
  before, the services behind it.**
