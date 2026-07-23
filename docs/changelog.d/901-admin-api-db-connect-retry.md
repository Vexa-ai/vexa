- **admin-api retries the initial DB connect on cold start (#901).** On a boot where Postgres DNS
  isn't resolvable yet, admin-api used to throw `socket.gaierror` and exit immediately, relying on
  the k8s restart loop (a transient RED an operator would see). It now retries the first connect
  with bounded exponential backoff (env-tunable via `DB_CONNECT_MAX_ATTEMPTS` /
  `DB_CONNECT_BASE_DELAY` / `DB_CONNECT_MAX_DELAY`), then fails loud once the bound is exhausted.
