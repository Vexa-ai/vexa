# deploy — the v0.12 container composition (Compose)

Brings up the whole v0.12 control plane as one ordered, health-gated stack: the infra
(`postgres:17` · `redis:7` · `minio` + `minio-init`) and the four long-running Python
services — **admin-api · runtime · agent-api · meeting-api · gateway** — each building its own
slim `uv` image, plus the dev hot-reload overlay and the dashboard overlay. This is the
*composition* layer (it owns no service code); it also owns the **`execution-targets.v1`**
contract — the machine-readable "where can work run, and what does it need?" registry a plan
resolves against in planning, before execution (ADR-0020).

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| spawns-over | the 4 core services + dashboard | `<service>/Dockerfile` build + `python -m <pkg>` | container images, env wiring, ordered health-gated bring-up |
| spawns-over | infra | `redis:7-alpine` · `postgres:17-alpine` · `minio` + `minio/mc` | the backing stores every service `depends_on: service_healthy` |
| spawns-over | bot | `runtime` → `/var/run/docker.sock`, `BROWSER_IMAGE=vexaai/vexa-bot:v012` | on-demand per-meeting bot container (published image, never built here) |
| produces | `scripts/gates.mjs` (`gate:execution-env`) + planning preflight | `execution-targets.v1` JSON (`deploy/execution-targets.json`, gitignored) | the resolved targets[]/resources[] registry; `secret_ref` references only (P14) |
| calls | host secret store | `secret_ref: vexa-secrets:<path>` / `env:<NAME>` | references to credentials — never inline secret values |
| publishes | dev loop | `docker-compose.dev.yml` source bind-mounts + `watchfiles` | host checkout → process restart, no image rebuild |

## Contracts

**Owns:** [`deploy/contracts/execution-targets.v1`](contracts/execution-targets.v1/) — the
host/user-specific execution-target & resource registry (sealed in
[`contracts.seal.json`](../contracts.seal.json); a leaf contract, depends on nothing).
**Consumes:** none — the compose files *reference* the sealed service/invocation/lifecycle/
runtime/schedule schemas indirectly (each service vendors and validates its own by path); deploy
itself reads no `*.v1`.

## Lite — the single-container shape

[`deploy/lite`](lite/) is the all-in-one alternative to this compose stack: the SAME service code
in ONE container, with the runtime on the **process backend** (`RUNTIME_BACKEND=process`) so bots
and agent workers run as child processes instead of socket-spawned containers. `make lite` (root)
provisions postgres + minio sidecars and runs the rest in a single image — quick evaluation /
small teams; use this compose stack for dev / production. See [`deploy/lite/README.md`](lite/README.md).

## Isolated evaluation

`deploy/compose/tests/` — `stack_test.py` (the **`gate:compose`** proof) brings up the real
stack under an isolated project name, proves health + auth + transcript bus + recordings→minio +
lifecycle wiring, then `down -v` in a guaranteed finally (docker absent → green skip). The
`execution-targets.v1` contract is gated separately via `validate.mjs` against `golden/`.

```bash
make -C deploy/compose stack-test        # L3 integration — always-on subset (gate:compose)
make -C deploy/compose stack-test-bot    # L4 live — + real bot-spawn proof (COMPOSE_BOT=1, ~7GB)
node deploy/contracts/execution-targets.v1/validate.mjs   # L1 contract — schema + golden
```

## Status

- ✅ delivered — five-service + infra compose, every service health-gated with ordered `depends_on`
- ✅ delivered — `docker-compose.dev.yml` hot-reload overlay (source mounts + `watchfiles`)
- ✅ delivered — dashboard overlay (`docker-compose.dashboard.yml`) + `provision-token`
- ✅ delivered — runtime spawns the bot via host docker.sock (published image, not built)
- ✅ delivered — `execution-targets.v1` contract (sealed) + `gate:execution-env` + example template
- ✅ delivered — `gate:compose` real-stack readiness proof (`deploy/compose/tests/`)
