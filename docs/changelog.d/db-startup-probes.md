- **The chart's postgres and redis get a startup probe, so a slow first start isn't killed.** On a
  loaded node, the old postgres liveness probe (30 s delay, 1 s timeout) could restart postgres
  during its first-time initdb. The data directory was left half-initialised, and admin-api then
  failed with `no pg_hba.conf entry`. Liveness now starts only after the startup probe passes, every
  probe times out after 5 s, and `postgres.probes` / `redis.probes` set the budgets. Changing the
  probes changes the pod template, so postgres and redis restart once on upgrade. See
  [Kubernetes](/deployment-kubernetes#what-the-chart-deploys).
