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

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | Container created on POST /containers | 15 | ceiling | 0 | PASS | Containers created for all bots | 2026-04-05T19:40Z | 14-container-lifecycle, 07-bot-lifecycle |
| 2 | Container removed after meeting ends | 25 | ceiling | 0 | PARTIAL | Containers stopped but 7 zombie node processes remain unreap'd (BUG #20) | 2026-04-05T21:41Z | 14-container-lifecycle |
| 3 | No orphan containers after test run | 25 | ceiling | 0 | FAIL | 7 zombie node processes in Z/defunct state. Process backend lacks waitpid/child reaper (BUG #20). | 2026-04-05T21:41Z | 14-container-lifecycle |
| 4 | Container respects resource limits (CPU, memory, shm) | 15 | — | 0 | SKIP | Not tested this run | 2026-04-05T19:40Z | 14-container-lifecycle |
| 5 | idle_timeout stops inactive containers | 20 | — | 0 | SKIP | Not tested this run | 2026-04-05T19:40Z | 14-container-lifecycle, 07-bot-lifecycle |

Confidence: 15 (ceiling item #3 FAIL — 7 zombie node processes in lite mode due to BUG #20. Containers logically complete but processes not reaped. In compose mode Docker handles cleanup; lite mode's process backend lacks waitpid.)
