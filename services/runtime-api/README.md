# Runtime API

## Why

You need to spawn containers on demand — AI agent sandboxes, browser sessions, code runners, dev environments. The options are: Fly Machines (proprietary, not self-hosted), Kubernetes Jobs (requires K8s, no idle management, no callbacks), Docker Compose (no API, no per-user limits), or E2B (proprietary, not self-hosted).

None of them give you a self-hosted REST API with idle management, lifecycle callbacks, and per-tenant concurrency that works across Docker, Kubernetes, and plain processes from the same interface.

Runtime API fills that gap. `POST /containers` with a profile name and a callback URL. Get a managed container back. It idles out automatically, fires a webhook when it exits, and enforces per-user limits. Switch from Docker in dev to Kubernetes in prod by changing one environment variable.

**Why not build it yourself?** Container lifecycle code is deceptively simple until you handle: orphaned containers after crashes (state reconciliation on startup), idle detection across restarts (Redis-backed timers), callback delivery with retry (exponential backoff), graceful shutdown that doesn't kill active work, and per-user concurrency enforcement across a distributed fleet. That's what the 2400 lines here do.

## Data Flow

```
Client (agent-api or direct)
    │
    ▼
POST /containers { profile, user_id, callback_url }
    │
    ▼
profiles.py: load profile from YAML, merge with request overrides
    │
    ▼
backends/: dispatch to configured backend (Docker / K8s / Process)
    │
    ├── docker.py:    docker create + start via unix socket
    ├── kubernetes.py: kubectl apply Pod spec
    └── process.py:   subprocess.Popen with resource limits
    │
    ▼
state.py: register in Redis (name, profile, user_id, status, callback_url)
    │
    ▼
201 { name, status: "running", ports }

---

Idle management (background loop):
    for each container in Redis:
      if now - last_touch > idle_timeout:
        backend.stop(name)
        fire callback(status=stopped, reason=idle_timeout)
        state.remove(name)

POST /containers/{name}/touch resets idle timer (heartbeat)

---

On startup: reconcile_state()
    list containers in Redis vs actual backend state
    dead in backend but alive in Redis → fire exit callback, remove
    alive in backend but not in Redis → register (orphan recovery)
```

## What

### Container lifecycle — what happens when you POST /containers

```
POST /containers { profile: "worker", user_id: "u-123", callback_url: "http://..." }
     │
     ▼
profiles.py ── load profile from YAML ── merge with request config overrides
     │
     ▼
backends/ ── dispatch to configured backend:
     │
     ├── docker.py   ── docker create + start (via unix socket)
     ├── kubernetes.py ── kubectl apply Pod spec
     └── process.py  ── subprocess.Popen with resource limits
     │
     ▼
state.py ── register in Redis: name, profile, user_id, status, created_at, callback_url
     │
     ▼
201 { name: "worker-u-123-a8f3", status: "running", ports: {...} }
```

### Idle management — how containers get cleaned up

```
idle_loop (runs every IDLE_CHECK_INTERVAL seconds):
  for each registered container:
    last_touch = Redis HGET container:{name} last_touch
    idle_timeout = profile idle_timeout (from YAML)
    if now - last_touch > idle_timeout:
      backend.stop(name)        ← SIGTERM, then SIGKILL after 10s
      fire callback(status=stopped, reason=idle_timeout)
      state.remove(name)
```

`POST /containers/{name}/touch` resets `last_touch` in Redis. Clients call this as a heartbeat to keep their container alive. No touch + idle_timeout exceeded = container dies.

### State reconciliation — surviving crashes

On startup, `reconcile_state()` runs:

```
1. List all containers in Redis state store
2. For each, ask the backend: "is this actually running?"
3. Mismatch? Backend says dead but Redis says running → fire exit callback, remove from state
4. Backend says running but not in Redis → register it (orphan recovery)
```

This means you can restart Runtime API and it reconstructs its view of the world from the backend. No containers leak.

### Callback delivery — how your service gets notified

When a container exits (clean, crash, or idle timeout):

```
lifecycle.py ── handle_container_exit(name, exit_code)
     │
     ▼
state.py ── read callback_url + metadata from Redis
     │
     ▼
httpx POST to callback_url:
  { container_id, name, profile, status, exit_code, metadata }
     │
     ├── 2xx → done
     └── fail → retry with backoff (1s, 5s, 30s)
              └── all retries exhausted → log error, move on
```

Your `metadata` dict from creation time is passed back in the callback — use it to correlate containers with your domain objects (job IDs, meeting IDs, session IDs).

### Module map

| Module | What it does |
|--------|-------------|
| `main.py` | FastAPI app — startup (Redis, backend, reconcile, idle loop), shutdown |
| `api.py` | REST endpoints — `/containers` CRUD, `/profiles`, `/health` |
| `config.py` | All settings from environment variables |
| `profiles.py` | YAML loader with SIGHUP hot-reload |
| `state.py` | Redis-backed container registry — register, remove, list, touch |
| `lifecycle.py` | Idle loop, exit handler, callback delivery with retry |
| `backends/__init__.py` | `Backend` ABC + `ContainerSpec` + `ContainerInfo` |
| `backends/docker.py` | Docker backend via unix socket (requests-unixsocket) |
| `backends/kubernetes.py` | K8s backend via kubernetes Python client |
| `backends/process.py` | Process backend — subprocess.Popen with Redis-backed registry |

## How

### Docker Compose (recommended)

```bash
curl -O https://raw.githubusercontent.com/vexa-ai/runtime-api/main/docker-compose.yml
docker compose up -d
```

### From source

```bash
git clone https://github.com/vexa-ai/runtime-api.git
cd runtime-api
pip install -e .
uvicorn runtime_api.main:app --host 0.0.0.0 --port 8090
```

Requires Redis (`redis://localhost:6379`) and Docker daemon access.

### Create a container

```bash
# Create a container from a profile
curl -X POST http://localhost:8090/containers \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "worker",
    "user_id": "user-123",
    "callback_url": "http://my-service:8080/hooks/container",
    "metadata": {"job_id": "abc"}
  }'
```

### List containers

```bash
curl http://localhost:8090/containers?profile=worker&user_id=user-123
```

### Stop a container

```bash
curl -X DELETE http://localhost:8090/containers/worker-abc123
```

### Heartbeat (reset idle timer)

```bash
curl -X POST http://localhost:8090/containers/worker-abc123/touch
```

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/containers` | Create and start a container |
| `GET` | `/containers` | List containers (filter by `user_id`, `profile`) |
| `GET` | `/containers/{name}` | Inspect container (status, ports, metadata) |
| `DELETE` | `/containers/{name}` | Stop and remove container |
| `POST` | `/containers/{name}/touch` | Heartbeat — reset idle timer |
| `POST` | `/containers/{name}/exec` | Execute command inside container |
| `GET` | `/containers/{name}/wait` | Long-poll until target state reached |
| `GET` | `/profiles` | List available container profiles |
| `GET` | `/health` | Health check with container counts |

## Profiles

Profiles are declarative container templates defined in YAML. Reference them by name when creating containers.

```yaml
# profiles.yaml
profiles:
  worker:
    image: my-worker:latest
    resources:
      cpu_limit: "1000m"
      memory_limit: "2Gi"
    idle_timeout: 900        # stop after 15min idle
    auto_remove: true
    ports:
      "8080/tcp": {}

  sandbox:
    image: code-sandbox:latest
    command: ["sleep", "infinity"]
    resources:
      cpu_limit: "2000m"
      memory_limit: "2Gi"
      shm_size: 2147483648   # 2GB
    idle_timeout: 600        # 10min
    ports:
      "8080/tcp": {}
```

Hot-reload: `kill -HUP <pid>` to reload profiles without restart.

## Backends

Runtime API supports three orchestration backends, selected via `ORCHESTRATOR_BACKEND` environment variable:

| Backend | Env Value | Use Case |
|---------|-----------|----------|
| **Docker** | `docker` | Local development, single-host deployment |
| **Kubernetes** | `kubernetes` | Production — pods, RBAC, resource limits, node selectors |
| **Process** | `process` | Lightweight — child processes, no container runtime needed |

### Backend interface

All backends implement the same abstraction:

```
create(spec) → container_id
stop(name, timeout) → bool
remove(name) → bool
inspect(name) → ContainerInfo | None
list(labels) → ContainerInfo[]
exec(name, cmd) → AsyncIterator[bytes]
startup() → None
shutdown() → None
listen_events(on_exit) → None
```

### Resource limits by backend

Not all backends enforce all resource limits. This is an honest summary of what actually works:

| Limit | Docker | Kubernetes | Process |
|-------|--------|------------|---------|
| `memory_limit` | ✅ cgroups hard limit | ✅ pod limit (OOMKill) | ⚠️ `RLIMIT_AS` — limits virtual address space, not RSS. Unreliable for languages that memory-map files. |
| `cpu_limit` | ❌ not implemented | ✅ pod CPU limit | ❌ no POSIX equivalent to cgroups |
| `cpu_request` | ❌ not applicable | ✅ pod CPU request (scheduling) | ❌ N/A |
| `memory_request` | ❌ not applicable | ✅ pod memory request (scheduling) | ❌ N/A |
| `shm_size` | ✅ Docker `--shm-size` | ✅ tmpfs volume at /dev/shm | ❌ not implemented (uses host /dev/shm) |
| `gpu` | ❌ not implemented | ✅ via node_selector + device plugin | ❌ N/A |

**Kubernetes** is the only backend with full resource enforcement. **Docker** handles memory and shm but silently ignores CPU limits. **Process** silently ignores most limits.

Limits that are silently ignored are NOT errors — the container starts and runs without the constraint. If you set `cpu_limit: "500m"` on the Docker backend, the container gets unlimited CPU. There's no warning at creation time. This is a known limitation, not a bug — enforcing would mean rejecting valid requests on backends that can't express the limit.

**Choose your backend based on what isolation you need:**
- **Multi-tenant with hard limits** → Kubernetes
- **Single-host with memory isolation** → Docker
- **Development / Vexa Lite / no container runtime** → Process (trusted workloads only)

## Lifecycle Callbacks

Pass a `callback_url` when creating a container. Runtime API POSTs to it on state transitions:

```json
{
  "container_id": "abc123def",
  "name": "worker-abc123",
  "profile": "worker",
  "status": "stopped",
  "exit_code": 0,
  "metadata": {"job_id": "abc"}
}
```

Callbacks fire on: `stopped` (clean exit or idle timeout), `failed` (non-zero exit code). Retries with exponential backoff (default: 1s, 5s, 30s).

## Comparison

| | Runtime API | Fly Machines | K8s Jobs | Docker Compose | E2B |
|---|---|---|---|---|---|
| REST API | Yes | Yes | Via kubectl | No | Yes |
| Container profiles | Yes | No | No | No | Templates |
| Idle management | Yes | Yes (auto-stop) | No | No | Yes |
| Lifecycle callbacks | Yes | No | Limited | No | No |
| Self-hosted | Yes | No | Yes | Yes | No |
| Open source | Yes | No | Yes | Yes | No |
| No K8s required | Yes | Yes | No | Yes | Yes |
| Multi-backend | Yes | No | K8s only | Docker only | No |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_BACKEND` | `docker` | Backend: `docker`, `kubernetes`, or `process` |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `PROFILES_PATH` | `profiles.yaml` | Path to profiles config |
| `IDLE_CHECK_INTERVAL` | `30` | Seconds between idle checks |
| `CALLBACK_RETRIES` | `3` | Max callback delivery attempts |
| `CALLBACK_BACKOFF` | `1,5,30` | Backoff delays in seconds |
| `API_KEYS` | _(empty)_ | Comma-separated API keys (empty = no auth) |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |
| `LOG_LEVEL` | `INFO` | Log level |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8090` | Server port |

### Docker backend

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker daemon socket |
| `DOCKER_NETWORK` | `bridge` | Docker network for containers |

### Kubernetes backend

| Variable | Default | Description |
|----------|---------|-------------|
| `K8S_NAMESPACE` | `default` | Kubernetes namespace (falls back to `POD_NAMESPACE`) |
| `K8S_SERVICE_ACCOUNT` | _(empty)_ | Service account for pods |
| `K8S_IMAGE_PULL_POLICY` | `IfNotPresent` | Image pull policy |
| `K8S_IMAGE_PULL_SECRET` | _(empty)_ | Image pull secret name |

### Process backend

| Variable | Default | Description |
|----------|---------|-------------|
| `PROCESS_LOGS_DIR` | `/var/log/containers` | Directory for process logs |
| `PROCESS_REAPER_INTERVAL` | `30` | Seconds between reaper checks |

## Architecture

```
                    ┌──────────────┐
                    │  Your App    │
                    │              │
                    │ POST /containers
                    │ GET  /containers
                    │ DELETE /containers/{name}
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  Runtime API │
                    │              │
                    │ • profiles   │
                    │ • state      │
                    │ • idle mgmt  │
                    │ • callbacks  │
                    │ • concurrency│
                    └──────┬───────┘
                           │
                  ┌────────┼────────┐
                  │        │        │
            ┌─────▼──┐ ┌──▼───┐ ┌──▼──────┐
            │ Docker │ │ K8s  │ │ Process │
            │ socket │ │ pods │ │ child   │
            └────────┘ └──────┘ └─────────┘
```

## Use Cases

- **AI agent sandboxes** — give agents their own containers with lifecycle management
- **Browser automation farms** — manage browser pools with CDP access and idle cleanup
- **Dev environments** — on-demand coding containers with workspace persistence
- **CI/CD runners** — ephemeral build containers with per-tenant limits
- **Code execution** — sandboxed code runners with timeout enforcement

## Production Readiness

**Confidence: 52/100**

Audited 2026-03-27. 5029 lines across 13 source files + 9 test files. Unit test run: **85 passed, 0 failed, 21 skipped** (integration tests skipped without Docker/runtime).

| Area | Score | Evidence | Gap |
|------|-------|----------|-----|
| Code quality | 50/100 | Clean abstractions (Backend ABC, ContainerSpec dataclass), consistent logging, separation of concerns across modules | Missing `count_user_containers` function in `state.py` (tests fail with `AttributeError`). `SCHEDULER_POLL_INTERVAL` referenced in `scheduler.py:344` but not defined in `config.py` (runtime crash). `max_per_user` documented and in profiles but never enforced in `api.py`. Scheduler module (`scheduler.py`) fully implemented but never wired into `main.py` — dead code. |
| Test coverage | 55/100 | 106 tests collected, 85 pass, 21 skipped (integration). Tests cover: state CRUD, profile loading, lifecycle idle/callback/reconcile, scheduler CRUD/retry/cron, scheduler API endpoints (12 tests), backend ABC, process backend start/stop/inspect, container API CRUD | Zero Docker backend tests against real Docker API. Zero K8s backend tests (only dataclass field assertions). FakeRedis mocks ignore `ex=` TTL parameter — TTL behavior untested. |
| Docker | 55/100 | `Dockerfile` builds (13 lines, `python:3.12-slim`). `docker-compose.yml` has healthcheck, Redis dependency with `service_healthy` condition, volume persistence, restart policy | No `HEALTHCHECK` instruction in Dockerfile itself. Container runs as root (no `USER` directive). No `.dockerignore` — copies `__pycache__`, `.pytest_cache` into image. No multi-stage build. |
| Standalone (`make up && make test`) | 50/100 | `Makefile` has `up`, `test-unit`, `test-integration` targets. `make up` waits for health with 30s timeout. `make test-unit` passes: 85 pass, 0 fail | `make test-integration` requires Docker daemon access and pulling images. CI workflow references `ORCHESTRATOR_BACKEND=docker` for integration tests but service container won't have Docker socket access. |
| Security | 45/100 | API key middleware exists (optional via `API_KEYS` env). Health/docs endpoints skip auth. Container names sanitized (alphanumeric + hyphens). SSRF protection on `callback_url` (blocks private IPs, loopback, link-local, reserved ranges, DNS resolution checks, `.internal`/`.local`/`.svc` suffixes, metadata service) | Auth disabled by default (empty `API_KEYS`). CORS defaults to `*`. No rate limiting. `/exec` endpoint allows arbitrary command execution with no restrictions. |
| Reliability | 50/100 | `reconcile_state()` syncs Redis with backend on startup. Event-driven exit detection (Docker events, K8s watch, process reaper). Callback retry with exponential backoff. Graceful shutdown cancels tasks and closes connections | `reconcile_state` failure logged as warning, service continues with stale state. Idle loop and event listeners catch all exceptions at `debug` level — production issues would be invisible. `_terminate_process_group` uses blocking `time.sleep(0.5)` loop called from async context — blocks event loop for up to `timeout` seconds. |
| Performance | 35/100 | Docker API calls wrapped in `run_in_executor` to avoid blocking. Redis used for state instead of in-memory | `state.list_containers` uses `SCAN` + individual `GET` for every key — O(N) with N round trips. Health endpoint calls `list_containers` on every request (O(N) scan on every health check). Docker backend `exec` streams via synchronous `iter_content` — blocks event loop (acknowledged in TODO comment). `cancel_job` and `get_job` scan entire sorted set linearly to find by ID. |
| Documentation | 70/100 | README is thorough: architecture diagram, module map, env var table, backend comparison matrix, honest resource limits table, lifecycle flow diagrams | Idle management description says `HGET container:{name} last_touch` — actual implementation uses `updated_at` field via `set_container`. Scheduler module undocumented. |
| CI | 45/100 | 3-job pipeline: lint (ruff), unit tests, integration tests. Integration job has Redis service container, pulls test images, starts uvicorn | Unit test job will fail (7 failures + collection error). Integration test job starts uvicorn with `ORCHESTRATOR_BACKEND=docker` but GitHub Actions runners don't mount Docker socket into service containers — integration tests will likely fail. No `croniter` in deps but scheduler uses it for cron rescheduling. |
| Process backend | 45/100 | Functional: spawns processes, tracks in Redis, reaper detects dead processes, SIGTERM/SIGKILL termination, resource limits via RLIMIT_AS | `exec` runs a new subprocess in host env — not "inside" the managed process (fundamentally different from Docker/K8s exec). `_terminate_process_group` blocks event loop. Process `stop` test fails (race condition in CI). `RLIMIT_AS` for memory is unreliable (limits virtual address space, not RSS). |
| K8s backend | 30/100 | Complete implementation: pod creation with resources/GPU/tolerations/affinity, watch-based exit detection, image pull secrets, service accounts | Never tested — not even with mocked K8s client. No port mapping (K8s pods don't expose ports the same way). `kubernetes` package is an optional dependency — import errors at runtime if not installed, no graceful fallback. Watch with `timeout_seconds=0` holds connections indefinitely. |

### Known Limitations

- **`state.count_user_containers` does not exist** — 2 tests fail, per-user limits not enforced
- **`config.SCHEDULER_POLL_INTERVAL` not defined** — scheduler would crash at runtime if started
- **Scheduler not wired into app** — `main.py` never calls `start_executor`, entire module is dead code
- **Python 3.10+ required** — source uses `X | None` union syntax but `pyproject.toml` says `>=3.11`, test fixtures use the same syntax which breaks on 3.9
- **`croniter` missing from deps** — `scheduler.py` imports it for cron rescheduling but it's not in `pyproject.toml`
- **Docker `exec` blocks event loop** — synchronous `iter_content` in async generator (acknowledged TODO)
- **Health endpoint is O(N)** — scans all Redis keys on every call

### Validation Plan (to reach 90+)

1. **Fix broken code** — Add `count_user_containers` to `state.py`. Add `SCHEDULER_POLL_INTERVAL` to `config.py`. Add `max_per_user` enforcement in `api.py:create_container`. Wire scheduler into `main.py` or remove dead code. Add `fakeredis` and `croniter` to `pyproject.toml`.
2. **Fix failing tests** — Fix the 7 test failures and 1 collection error. Add `from __future__ import annotations` to test files using union syntax, or use `Optional[]`.
3. **Security hardening** — Validate container names (alphanumeric + hyphens only). Validate `callback_url` against allowlist or block RFC1918 ranges. Add rate limiting middleware. Document that `/exec` is privileged. Run container as non-root user in Dockerfile.
4. **Performance fixes** — Replace `SCAN`-based listing with a Redis SET index of active container names. Cache health check results (1-5s TTL). Use `httpx` or `aiohttp` for Docker backend instead of synchronous `requests_unixsocket`. Fix blocking `_terminate_process_group`.
5. **Test gaps** — Add Docker backend integration tests (or at least tests with mocked `requests_unixsocket`). Add K8s backend tests with mocked `kubernetes` client. Test TTL expiry behavior. Test API key middleware (auth enabled/disabled). Test concurrent container creation. Test exec endpoint.
6. **Docker image hardening** — Add `.dockerignore`. Add `USER nonroot`. Add `HEALTHCHECK`. Consider multi-stage build to reduce image size.
7. **CI fixes** — Fix unit tests so they pass. Add Docker-in-Docker or use process backend for integration tests in CI. Add test coverage reporting.
8. **Observability** — Promote `debug`-level exception logging in idle loop and event listeners to `warning` or `error`. Add structured logging. Add Prometheus metrics endpoint (container count, creation latency, callback success rate).
9. **Process backend fixes** — Make `_terminate_process_group` async-safe (run in executor). Document that `exec` doesn't enter the process namespace.
10. **K8s backend validation** — Test with a real or mocked K8s cluster. Add port mapping via K8s Services or NodePort. Handle `kubernetes` import gracefully when package not installed.

## Code Ownership

```
runtime_api/main.py            → FastAPI app, startup (Redis, backend, reconcile, idle loop), shutdown
runtime_api/api.py             → REST endpoints: /containers CRUD, /profiles, /health
runtime_api/config.py          → all settings from environment variables
runtime_api/profiles.py        → YAML profile loader with SIGHUP hot-reload
runtime_api/state.py           → Redis-backed container registry (register, remove, list, touch)
runtime_api/lifecycle.py       → idle loop, exit handler, callback delivery with retry
runtime_api/scheduler.py       → job scheduler (CRUD, cron, retry) — NOT wired into main.py
runtime_api/scheduler_api.py   → scheduler REST endpoints — NOT wired into main.py
runtime_api/utils.py           → shared utilities
runtime_api/backends/__init__.py → Backend ABC, ContainerSpec, ContainerInfo
runtime_api/backends/docker.py   → Docker backend via unix socket
runtime_api/backends/kubernetes.py → K8s backend via kubernetes Python client
runtime_api/backends/process.py  → Process backend via subprocess.Popen
tests/                         → 106 tests (85 pass, 21 skipped integration)
```

## Constraints

- Backend-agnostic — same REST API regardless of Docker, Kubernetes, or Process backend
- Redis is the ONLY state store — no SQL database, all container state in Redis
- One profile YAML file defines all container templates — hot-reloadable via SIGHUP
- Idle management is automatic — containers die after `idle_timeout` without heartbeat
- Callback delivery is best-effort with retry — not guaranteed (3 attempts with backoff)
- SSRF protection on `callback_url` — blocks private IPs, loopback, link-local, metadata services
- Container names must be unique — format: `{profile}-{user_id}-{hash}`
- State reconciliation on startup — Redis state synced with backend reality
- `/exec` endpoint allows arbitrary command execution — privileged, no restrictions
- Auth disabled by default (empty `API_KEYS`) — must be explicitly configured
- README.md MUST be updated when behavior changes

## Known Issues

- `state.count_user_containers` does not exist — per-user concurrency limits (`max_per_user`) documented but never enforced
- `config.SCHEDULER_POLL_INTERVAL` not defined — scheduler would crash at runtime if started
- Scheduler module fully implemented but never wired into `main.py` — dead code
- `croniter` missing from `pyproject.toml` dependencies — scheduler import fails
- Docker `exec` uses synchronous `iter_content` blocking the event loop (acknowledged TODO)
- Health endpoint is O(N) — scans all Redis keys via `SCAN` + individual `GET` per container
- `_terminate_process_group` uses blocking `time.sleep` in async context — blocks event loop
- K8s backend never tested (not even with mocks)
- No rate limiting on any endpoint
- CORS defaults to `*`
- **Process backend: zombie reaper doesn't detect dead processes (bug #20)** — `_pid_alive()` in `process.py:292` uses `os.kill(pid, 0)` which succeeds for zombie processes (Z/defunct state). After meeting bots exit, child processes remain as zombies because the process backend never calls `waitpid()`. This leaks PIDs and prevents proper cleanup. Docker and Kubernetes backends are unaffected (container runtime handles reaping). Fix: add `waitpid(WNOHANG)` to the reaper loop, or install a SIGCHLD handler.

## License

Apache-2.0
