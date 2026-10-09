# runtime_kernel — the kernel

Conforms to `runtime.v1` (and `schedule.v1` for the scheduler). Files:

- `models` — the v1 shapes as Pydantic, validated against the schema in tests.
- `backend` — the Backend port; `process_backend` / `docker_backend` / `k8s_backend` implement it.
- `profiles` — the opaque-profile → Runnable registry (P11) + the real `meeting-bot` / `agent` profiles.
  A Runnable carries what its kind of workload is given beyond its spec (labels, network setting,
  forwarded settings, credential files and settings, host groups, Pod placement) as data; no backend
  names a harness. One residue: `docker_backend._worker_naming` renames an `agent-…` workload's
  container to `worker-…` and labels it, keying on agent-api's id scheme. `configured_credentials` is the one place that
  maps the operator's credential settings to files.
- `pod_scheduling` — a profile's Pod placement on Kubernetes (node selector, tolerations, priority class,
  image pull secrets), read from `RUNTIME_K8S_BOT_*` / `RUNTIME_K8S_AGENT_WORKER_*` and validated at boot.
- `isolation` — the identity of every process-backend child under a root runtime: subject uid or a
  per-workload uid, a fresh private HOME, the store's ownership — never root, never through a link.
- `store` — the WorkloadStore port (persistence): `InMemoryStore` (default) + `RedisStore` (durable).
- `clock` — the Clock port (`SystemClock` / `FakeClock`) so enforcement + scheduler are deterministic.
- `kernel` — the lifecycle orchestrator over the store; quotas via `count_for_owner`.
- `enforcement` — the reaper: stops workloads past idle/max-lifetime limits via the Clock.
- `scheduler` — the redis sorted-set job scheduler (one-shot/cron, retry/backoff, idempotency, orphan recovery).
- `callbacks` — durable RuntimeEvent delivery (a CallbackQueue that retries until the receiver acks).
- `workload_env` — what a spec may carry versus what the runtime decides: runtime-owned keys dropped,
  the mount set checked against the runtime's own workspace store and its configured out-of-store
  sources (`RUNTIME_EXTRA_MOUNT_SOURCES`), the process backend's child env.
- `caller_auth` — the runtime caller credential (`RUNTIME_API_TOKEN`) every route but `/health` requires.
- `api` — the FastAPI surface (create/get/list/stop/destroy + `/health`).

Depends on nothing above it.
