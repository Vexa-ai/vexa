# Container Lifecycle

## Why

Bot containers are ephemeral. They must be cleaned up after use — no orphan containers consuming resources. The runtime-api manages creation, monitoring, and cleanup. Leaked containers from crashes, timeouts, or bugs must be detected and removed.

## What

```
runtime-api creates container → monitors via Docker events
  → container exits → runtime-api updates state → removes container
  → periodic sweep catches orphans (containers with no active meeting)
```

### Components

| Component | File | Role |
|-----------|------|------|
| container backend | `services/runtime-api/runtime_api/backends/docker.py` | Create, start, stop, remove |
| container state | `services/runtime-api/runtime_api/api.py` | Track active containers in memory |
| profiles | `services/runtime-api/profiles.yaml` | Resource limits, timeouts, auto_remove |
| orphan cleanup | `services/runtime-api/` | Periodic scan for untracked containers |

## How

### 1. Observe container creation

When a bot is created via `POST /bots`, the runtime-api creates a Docker container. Inspect running containers:

```bash
# List bot containers managed by runtime-api
curl -s -H "X-API-Key: $VEXA_API_KEY" \
  http://localhost:8090/containers
# [{"container_id": "abc123...", "meeting_id": 135, "status": "running", ...}]
```

### 2. Verify container cleanup after meeting ends

After `DELETE /bots/{platform}/{id}`, the container stops and is removed:

```bash
# Stop the bot
curl -s -X DELETE -H "X-API-Key: $VEXA_API_KEY" \
  http://localhost:8056/bots/gmeet/135

# After a few seconds, check containers
docker ps --filter "label=vexa" --format "{{.Names}} {{.Status}}"
# (bot container no longer listed)
```

### 3. Check for orphan containers

```bash
# List all Vexa containers (including exited)
docker ps -a --filter "label=vexa" --format "{{.Names}} {{.Status}}"

# Check for zombie processes inside a running service container
cat /proc/*/status 2>/dev/null | grep -c "^State.*Z"
# 0
```

### 4. Inspect resource profiles

Container resource limits (CPU, memory, shm-size) are defined in `profiles.yaml`:

```bash
curl -s http://localhost:8090/profiles
# {"default": {"cpu_limit": "2.0", "memory_limit": "4g", "shm_size": "2g", ...}}
```

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | Container created on POST /containers | 15 | ceiling | 0 | PASS | Bot container created (201), visible in docker ps within 10s | 2026-04-07 | containers, bot |
| 2 | Container fully removed after meeting ends (not just stopped) | 25 | ceiling | 0 | PASS | FIX: added `backend.remove(name)` in `on_exit` callback (runtime-api/main.py). Container gone from `docker ps -a` after stop. ZOMBIE_COUNT=0. | 2026-04-07 | containers |
| 3 | No orphan containers after test run | 25 | ceiling | 0 | PASS | ORPHAN_COUNT=0. Cleaned 20 pre-existing zombies from before fix. New runs leave zero exited containers. | 2026-04-07 | containers |
| 4 | Container respects resource limits (CPU, memory, shm) | 15 | — | 0 | UNTESTED | | | containers |
| 5 | idle_timeout stops inactive containers | 20 | — | 0 | UNTESTED | | | containers, bot |
| 6 | Browser session container removed after destroy | 10 | — | 0 | PASS | browser tier 1: destroy→verify container count=0 in docker ps -a | 2026-04-07 | browser |
| 7 | Graceful shutdown saves state before container removal | 10 | — | 0 | UNTESTED | browser session should save to S3 on SIGTERM before exit | | browser |

Confidence: 75 (ceiling items 1-3 PASS = 65. Browser cleanup PASS = +10. Resource limits and idle_timeout untested = -25.)

### Fixes applied this run

| Bug | Fix | File | Evidence |
|-----|-----|------|----------|
| Exited containers not removed (20 zombies accumulated) | Added `backend.remove(name)` in `on_exit` callback after `handle_container_exit` | `services/runtime-api/runtime_api/main.py:84-87` | ZOMBIE_COUNT went from 20 → 0 |
