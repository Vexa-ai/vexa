---
id: test/container-lifecycle
type: validation
requires: [test/infra-up]
produces: [LIFECYCLE_OK]
validates: [container-lifecycle, bot-lifecycle, remote-browser]
docs: [features/container-lifecycle/README.md, features/bot-lifecycle/README.md, features/remote-browser/README.md, services/runtime-api/README.md]
mode: machine
---

# Container Lifecycle — No Zombies

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Verify that finished containers are **REMOVED**, not just stopped. Applies to all three deploy targets.

## Why

Zombie containers leak memory, disk, and PIDs. In production with hundreds of meetings/day, stopped-but-not-removed containers accumulate until the host runs out of resources.

## Inputs

| Name | From | Default | Description |
|------|------|---------|-------------|
| GATEWAY_URL | test/infra-up | — | API gateway URL |
| ADMIN_URL | test/infra-up | — | Admin API URL |
| API_TOKEN | test/api-full | — | Token with bot+browser scope |
| DEPLOY_MODE | test/infra-up | compose | compose, lite, or helm |

## Container types

| Type | Create | Stop | Expected result |
|------|--------|------|-----------------|
| Runtime container | POST /containers `{profile: "agent"}` | DELETE /containers/{id} | Container removed from `docker ps -a` |
| Meeting bot | POST /bots `{platform: "google_meet", native_meeting_id: "test-lifecycle"}` | POST /bots/google_meet/{id}/stop | Status → completed, container removed |
| Browser session | POST /bots `{mode: "browser_session"}` | DELETE /bots/browser_session/{id} | Container removed |

## Procedure

### 1. Baseline

Record current state before test:

```bash
# compose
docker ps -a --filter "name=meeting-" --filter "name=agent-" --format '{{.Names}} {{.Status}}' > /tmp/baseline.txt

# lite
docker exec $CONTAINER supervisorctl status > /tmp/baseline.txt

# helm
kubectl get pods -l app.kubernetes.io/part-of=vexa --no-headers > /tmp/baseline.txt
```

### 2. Create → Stop → Verify (for each container type)

For each of the 3 container types:

1. **Create** — POST to API, capture container ID
2. **Wait** — poll until status is active/running
3. **Stop** — DELETE or POST stop
4. **Wait** — poll until status is completed/stopped (max 30s)
5. **Verify removal** — check that the container/process is gone:

```bash
# compose: container must not appear in docker ps -a
docker ps -a --filter "name=$CONTAINER_NAME" --format '{{.Names}}' | grep -c "$CONTAINER_NAME"
# Expected: 0

# lite: process must not appear
docker exec $LITE_CONTAINER ps aux | grep -c "$BOT_PID"
# Expected: 0

# helm: pod must not exist
kubectl get pod $POD_NAME --no-headers 2>&1 | grep -c "NotFound"
# Expected: 1
```

### 3. Bulk cleanup test

Create 3 containers of mixed types, stop all, verify all removed:

1. Create: 1 runtime + 1 meeting bot + 1 browser session
2. Stop all three
3. Wait 30s
4. `docker ps -a` count must equal baseline count (zero new containers)

### 4. Zombie detection

Check for pre-existing zombies from previous test runs:

```bash
# compose: any stopped containers from test runs?
docker ps -a --filter "status=exited" --filter "name=meeting-" --format '{{.Names}} {{.Status}}'

# lite: any zombie processes?
docker exec $CONTAINER ps aux | grep -E 'Z|defunct'
```

Report count. If > 0, log FINDING with container names/PIDs.

## Outputs

| Name | Description |
|------|-------------|
| LIFECYCLE_OK | true if all containers properly removed |
| ORPHAN_COUNT | Number of orphaned containers found |
| ZOMBIE_COUNT | Number of pre-existing zombies found |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| Container stopped but not removed | runtime-api only stops, doesn't `docker rm` | Add `docker rm` after `docker stop` in runtime-api container backend | Stop != remove — must explicitly remove |
| Lite zombie process (Z state) | No `waitpid()` in process backend | Add child process reaper to lite runtime-api | Process-based backends need explicit zombie reaping |
| Meeting bot container persists after completed | meeting-api doesn't call runtime-api delete after stop | Add cleanup hook in meeting-api bot finalization | Two-step: meeting-api stops bot logic, then must tell runtime-api to remove container |

## Docs ownership

After this test runs, verify and update:

- **features/container-lifecycle/README.md**
  - DoD table: update Status, Evidence, Last checked for all items: #1 (container created on POST /containers), #2 (container removed after meeting ends), #3 (no orphan containers), #4 (respects resource limits), #5 (idle_timeout stops inactive containers)
  - Components table: verify `services/runtime-api/runtime_api/backends/docker.py` (create/stop/remove), `services/runtime-api/runtime_api/api.py` (state tracking), `services/runtime-api/profiles.yaml` (resource limits, timeouts, auto_remove) paths are correct
  - Architecture: verify the documented flow `runtime-api creates container -> monitors via Docker events -> container exits -> updates state -> removes container` matches actual behavior — check `docker ps -a` before and after the test to confirm containers are truly removed, not just stopped
  - Confidence score: recalculate after updating statuses

- **features/bot-lifecycle/README.md**
  - DoD table: update Status, Evidence, Last checked for item #5 (timeout auto-stop works) — if this test exercises idle_timeout by creating containers and letting them time out
  - Note "14-container-lifecycle owns orphan sweep after everything stops": verify no containers from previous test runs remain in `docker ps -a`

- **features/remote-browser/README.md**
  - DoD table: update Status, Evidence, Last checked for item #6 (no orphan containers after session ends) — this test creates and destroys a browser_session container and verifies it is fully removed

- **services/runtime-api/README.md**
  - Container lifecycle section: verify POST `/containers` creates, DELETE `/containers/{name}` stops AND removes as documented — if the test finds containers are stopped but not removed, update the Known Limitations
  - Profiles section: verify `auto_remove: true` in profiles.yaml actually causes container removal (not just stop) — check docker backend behavior
  - State reconciliation: verify `reconcile_state()` on startup catches orphans from previous test runs as documented
  - Backends section: verify the active backend's actual behavior for stop+remove matches the documented Backend interface (`stop` + `remove` as separate operations)
  - Resource limits table: verify that the documented limits (memory_limit, cpu_limit, shm_size) for the active backend match actual container resource constraints observed via `docker inspect`
