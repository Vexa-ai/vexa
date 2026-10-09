- **Docker Compose: workloads off the control-plane network, and no default database password
  (#1784).** The runtime starts meeting bots on a `bots` network (meeting-api, redis, storage) and agent
  workers on a `workers` network (the gateway, redis, flows-api, and the optional `llm-shim` /
  `searxng`); neither reaches the runtime, Postgres, admin-api, agent-api or the terminal.
  `DB_PASSWORD` has no default any more and the `postgres` service refuses to start on an empty or
  published value; `make up` mints one for a new install, and
  `make -C deploy/compose rotate-db-password` moves an existing install off `postgres`. See
  [One-time steps after upgrading](/deployment#one-time-steps-after-upgrading).
