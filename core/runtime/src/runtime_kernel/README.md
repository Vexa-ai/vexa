# runtime_kernel — the kernel

Conforms to `runtime.v1` (and `schedule.v1` for the scheduler). Files:

- `models` — the v1 shapes as Pydantic, validated against the schema in tests.
- `backend` — the Backend port; `process_backend` / `docker_backend` / `k8s_backend` implement it.
- `profiles` — the opaque-profile → Runnable registry (P11) + the real `meeting-bot` / `agent` profiles.
- `store` — the WorkloadStore port (persistence): `InMemoryStore` (default) + `RedisStore` (durable).
- `clock` — the Clock port (`SystemClock` / `FakeClock`) so enforcement + scheduler are deterministic.
- `kernel` — the lifecycle orchestrator over the store; quotas via `count_for_owner`.
- `enforcement` — the reaper: stops workloads past idle/max-lifetime limits via the Clock.
- `scheduler` — the redis sorted-set job scheduler (one-shot/cron, retry/backoff, idempotency, orphan recovery).
- `callbacks` — durable RuntimeEvent delivery (a CallbackQueue that retries until the receiver acks).
- `api` — the FastAPI surface (create/get/list/stop/destroy + `/health`).

Depends on nothing above it.
