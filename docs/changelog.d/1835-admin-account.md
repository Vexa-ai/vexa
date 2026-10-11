- **The company layer has one writer: a dedicated admin account.** Set `VEXA_ADMIN_ACCOUNT` on
  admin-api (Helm `adminApi.adminAccount`) to a fresh address and sign in as it. That account is the
  only one that writes `_global` (pages, `POLICIES.md`, flows, reset), and it has nothing else: no
  desk, no shared workspaces, no meetings. The terminal shows it only **Company**. Every other
  account, including the instance admin's own, now reads `_global` and never writes it or offers
  **Company**. An existing account is never converted. With nothing configured, nobody writes
  `_global`. **Upgrade:** agent-api's `VEXA_GLOBAL_ADMIN_SUBJECTS` is retired and ignored (agent-api
  logs a warning if it is still set). Flows authoring from a chat (`flows_submit`,
  `flow_lifecycle`) now needs the admin account. See
  [The admin account](/minutes/workspaces#the-admin-account).
