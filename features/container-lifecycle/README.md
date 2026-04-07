# Container Lifecycle

> Proc: `tests2/src/containers.md`
> Infra: `tests2/src/infra.md`

## What

Runtime-api is a generic container orchestrator. It manages Docker containers for meeting bots, browser sessions, and agent containers. It handles creation, monitoring, and removal. It does NOT manage meeting logic, transcription, or platform-specific behavior — those are the consumer's responsibility (meeting-api, agent-api).

## Lifecycle management: two models

### Model 1: Consumer-managed (`idle_timeout: 0`)

Used by: **meeting bots**

The consumer (meeting-api) owns the full lifecycle:
- **Scheduler jobs** (`max_bot_time`) — server-side kill switch after N hours
- **Bot-side timers** (`no_one_joined`, `left_alone`, `max_wait_for_admission`) — bot self-exits
- **Exit callbacks** — bot reports exit, meeting-api updates DB
- **User DELETE** — explicit stop via API

Runtime-api is passive: create, monitor Docker events, remove on request. Never auto-stops.

### Model 2: Heartbeat-managed (`idle_timeout > 0`)

Used by: **agent containers** (via agent-api), **browser sessions** (via gateway), **workers**, **sandboxes**

The consumer heartbeats via `POST /containers/{name}/touch`. If no heartbeat within `idle_timeout` seconds, runtime-api kills the container.

| Consumer | How it heartbeats | idle_timeout |
|----------|-------------------|-------------|
| Agent-api | Explicit `/touch` calls from `container_manager.py` | 300s (5min) |
| Gateway | `/touch` on every `/b/{token}/*` request + periodic touch while WebSocket open | profile-defined |
| Worker/sandbox | Consumer must call `/touch` | 900s / 600s |

### Model 3: Browser session idle tracking (decided 2026-04-07, not yet implemented)

Browser sessions use the heartbeat model, but the heartbeat comes from the **gateway** — not from the consumer (meeting-api). The gateway is the single choke point for all browser session traffic.

Design:
1. `browser-session` profile with `idle_timeout` (e.g. 3600s / 1h)
2. Every `/b/{token}/*` HTTP request through the gateway calls `/touch` on the container
3. While a WebSocket connection is open (`/b/{token}/cdp`, `/b/{token}/vnc/websockify`), the gateway periodically calls `/touch` (e.g. every 60s)
4. When last WebSocket closes and no HTTP requests arrive, idle countdown starts
5. After `idle_timeout` with no activity → runtime-api stops and removes container

This means:
- Container lives while someone is using it (Playwright connected, VNC open, saving state)
- Container dies automatically when abandoned (tab closed, user forgot about it)
- No explicit heartbeat logic in meeting-api needed
- Start (`POST /bots`) and stop (`DELETE /bots`) still go through meeting-api

### Summary

| Profile | idle_timeout | Managed by | Heartbeat source | Safety net |
|---------|-------------|------------|-----------------|------------|
| meeting | 0 | meeting-api | none (scheduler + bot timers) | Scheduler job (max_bot_time) |
| browser-session | 3600s (1h) | gateway | `/touch` on every `/b/{token}/*` request + periodic while WS open | idle_timeout kills abandoned sessions |
| agent | 300s (5min) | agent-api | explicit `/touch` from container_manager.py | idle_timeout kills orphans |
| worker | 900s (15min) | runtime-api | consumer must `/touch` | idle_timeout |
| sandbox | 600s (10min) | runtime-api | consumer must `/touch` | idle_timeout |
| gpu-compute | 300s (5min) | runtime-api | consumer must `/touch` | idle_timeout |

## Container state machine

```
         POST /containers
              │
              ▼
         ┌─────────┐
         │ creating │
         └────┬─────┘
              │ container started
              ▼
         ┌─────────┐
         │ running  │◄── POST /touch resets updated_at
         └──┬──┬──┬─┘
            │  │  │
            │  │  └── idle_timeout exceeded (model 2) ─► stop + remove
            │  └───── DELETE /containers (explicit) ───► stop + remove
            └──────── Docker "die" event (self-exit) ──┐
                                                       │
                    ┌──────────────────────────────────┐│
                    │                                  ▼▼
               ┌────────┐                        ┌────────┐
               │stopped │                        │ failed │
               │(exit 0)│                        │(exit≠0)│
               └───┬────┘                        └───┬────┘
                   └──────────┬──────────────────────┘
                              │
                        fire exit callback
                        backend.remove(name)
                        Redis key TTL 24h
                              │
                              ▼
                         ┌────────┐
                         │removed │ (gone from docker ps -a)
                         └────────┘
```

## Components

| Component | File | Role |
|-----------|------|------|
| API | `services/runtime-api/runtime_api/api.py` | POST/DELETE/GET /containers, POST /touch, POST /exec |
| State | `services/runtime-api/runtime_api/state.py` | Redis: `runtime:container:{name}` |
| Lifecycle | `services/runtime-api/runtime_api/lifecycle.py` | Exit handler, idle loop, reconciliation |
| Docker backend | `services/runtime-api/runtime_api/backends/docker.py` | Create, stop, remove, Docker event stream |
| Profiles | `services/runtime-api/profiles.yaml` | Per-type: image, resources, idle_timeout, auto_remove |
| Scheduler | `services/runtime-api/runtime_api/scheduler.py` | Deferred HTTP jobs (used by meeting-api for max_bot_time) |

## Redis state

| Key | TTL | Content |
|-----|-----|---------|
| `runtime:container:{name}` | none (running) / 24h (stopped) | `{status, profile, user_id, container_id, created_at, updated_at, ports, callback_url, metadata}` |
| `runtime:callback:{name}` | 1h | Pending exit callback for retry |

`updated_at` is the idle clock. Set on every `set_container()` call — creation, `/touch`, `/exec`. The idle loop compares `now - updated_at` against `idle_timeout`.

## Idle loop

- Runs every 30s (`IDLE_CHECK_INTERVAL`)
- For each running container: `if now - updated_at > profile.idle_timeout → stop + remove`
- `idle_timeout: 0` → skip entirely (consumer-managed, model 1)
- Only `POST /touch` and `POST /exec` reset `updated_at`
- "Idle" = **no API interaction from consumer** — NOT "nothing happening inside the container"
- Runtime-api has no visibility into container internals (audio, network, CPU)

## Exit callback

When Docker reports a container "die" event:
1. `handle_container_exit(redis, name, exit_code)` → set status stopped/failed in Redis
2. `_fire_exit_callback()` → POST to `callback_url` with `{container_id, name, profile, status, exit_code, metadata}`
3. `backend.remove(name)` → delete container from Docker (added 2026-04-07)
4. Retry: 3 attempts, backoff 1s/5s/30s

## Reconciliation

`reconcile_state(redis, backend)` — runs once at API startup:
- Containers in Docker but not Redis → add to Redis
- Containers in Redis but not Docker → mark stopped
- Startup-only, not periodic

## Startup sequence

1. Connect Redis
2. Load profiles (hot-reload via SIGHUP)
3. Initialize Docker backend
4. Reconcile state
5. Start Docker event listener (die events)
6. Start idle loop (timeout enforcement)
7. Start scheduler executor (deferred jobs)

## DoD

| # | Check | Weight | Ceiling | Status | Last |
|---|-------|--------|---------|--------|------|
| 1 | POST /containers creates and starts container | 15 | ceiling | PASS | 2026-04-07 |
| 2 | Container fully removed after exit (not just stopped) | 25 | ceiling | PASS | 2026-04-07 |
| 3 | No orphan containers after test run | 25 | ceiling | PASS | 2026-04-07 |
| 4 | Resource limits match profile (docker inspect vs profiles.yaml) | 10 | — | UNTESTED | |
| 5 | idle_timeout kills containers without heartbeat (model 2) | 10 | — | UNTESTED | |
| 6 | `/touch` resets idle timer, keeps container alive past timeout | 10 | — | UNTESTED | |
| 7 | Exit callback delivered to callback_url | 10 | — | UNTESTED | |
| 8 | Reconciliation syncs state on API restart | 10 | — | UNTESTED | |
| 9 | Consumer-managed containers (model 1) not killed by idle loop | 10 | — | PASS | 2026-04-07 |
| 10 | Browser session dies after idle_timeout when no connections | 10 | — | NOT IMPLEMENTED | |
| 11 | Browser session stays alive while CDP/VNC WebSocket open | 10 | — | NOT IMPLEMENTED | |
| 12 | Gateway `/touch` on every `/b/{token}/*` request | 10 | — | NOT IMPLEMENTED | |
| 13 | Profile resource values based on measured usage | 10 | — | NOT DONE | |
| 14 | No `:latest` tags or `localhost` fallbacks in profiles | 5 | — | PASS | 2026-04-07 |
| 15 | Profiles propagate correctly to K8s (requests/limits/shm) | 10 | — | UNTESTED | |

## Profiles and resources

Profiles are defined in `services/runtime-api/profiles.yaml`. This is the single source of truth for all backends (Docker, K8s, process). Values propagate directly:
- Docker: `HostConfig.Memory`, `HostConfig.ShmSize`, CPU via cgroups
- K8s: `resources.requests`, `resources.limits`, emptyDir Memory volume for shm
- Process: resource limits via OS

**No fallback defaults.** If `${BROWSER_IMAGE}` or `${REDIS_URL}` is not set, the profile fails visibly. No `:latest` tags, no `localhost` fallbacks.

**`auto_remove: false`** for all profiles. Runtime-api removes containers explicitly after firing the exit callback and updating Redis state. Docker auto_remove would delete the container before runtime-api can process the exit.

**Resource values are not measured.** Current values (512Mi request, no memory limit) are placeholders. Meeting bots run Chrome + Playwright + audio capture — actual usage needs measurement under load. The comment in profiles.yaml notes: "No memory_limit — Chrome's virtual address space far exceeds physical RAM usage. RLIMIT_AS kills Chrome with SIGTRAP if set too low."

**K8s enforcement:** K8s will OOMKill pods that exceed memory_limit. Setting it too low kills Chrome. Setting it too high wastes cluster resources. Must be based on real measurements.

| Profile | cpu_request | cpu_limit | memory_request | memory_limit | shm_size |
|---------|-----------|---------|--------------|------------|---------|
| meeting | 500m | 2000m | 512Mi | none (Chrome needs headroom) | 2GB |
| browser-session | 500m | 2000m | 512Mi | none | 2GB |
| agent | 500m | 2000m | 512Mi | 2Gi | none |

## Known gaps

| Gap | Risk | Decision |
|-----|------|----------|
| Browser sessions run forever if not stopped | Resource leak on production hosts | Decided: `browser-session` profile with `idle_timeout: 3600` + gateway `/touch` on every `/b/{token}/*` request. Profile exists. Gateway heartbeat not yet implemented. |
| Resource values not measured | OOMKill on K8s, or wasted resources | Measure actual Chrome/bot memory under load. Set memory_request and memory_limit from real data. |
| Reconciliation is startup-only | Stale Redis state if event listener misses a die event | Acceptable — Docker events are reliable. Next restart catches stragglers. |
| No periodic orphan sweep | If meeting-api crashes mid-lifecycle, container stays until scheduler fires or restart | Meeting bots: scheduler is the safety net. Browser sessions: idle_timeout will be the safety net. |
| profiles.yaml `:latest` and `localhost` fallbacks removed | Container creation fails if env vars not set | Intentional — per "no fallbacks" rule. Env must be configured explicitly. |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| 20 exited containers accumulated | No `backend.remove()` in exit handler | Added `backend.remove(name)` in `on_exit` in main.py | Docker auto_remove=false means you own cleanup explicitly |
| Browser session runs forever | No idle_timeout, no scheduler job, no heartbeat | Design decided: idle_timeout + gateway heartbeat. Implementation pending. | Meeting-api manages meeting bots actively. Browser sessions were left unmanaged. |
