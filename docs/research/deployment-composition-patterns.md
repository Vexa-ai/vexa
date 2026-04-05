# Deployment Composition Patterns: Research Findings

> Research date: 2026-03-26
> Context: Vexa monorepo needs 5 deployment compositions (runtime-api standalone, +agent-api, +meeting-api, full stack, Vexa Lite)

---

## Table of Contents

1. [How Projects Manage Multiple Docker Compose Tiers](#1-how-projects-manage-multiple-docker-compose-tiers)
2. [Docker Compose Profiles vs Multiple Files vs Extends vs Include](#2-docker-compose-profiles-vs-multiple-files-vs-extends-vs-include)
3. [Deploy Directory Structure Patterns](#3-deploy-directory-structure-patterns)
4. [Testing Each Composition](#4-testing-each-composition)
5. [Helm Chart Patterns for Optional Components](#5-helm-chart-patterns-for-optional-components)
6. [Makefile / CLI Patterns](#6-makefile--cli-patterns)
7. [Recommendation for Vexa](#7-recommendation-for-vexa)

---

## 1. How Projects Manage Multiple Docker Compose Tiers

### Pattern A: Multiple Standalone Compose Files (Temporal)

**Project:** [Temporal](https://github.com/temporalio/docker-compose)

Temporal maintains **8 separate docker-compose files**, each a complete, standalone composition for a specific deployment scenario:

```
docker-compose.yml                        # PostgreSQL + Elasticsearch (default)
docker-compose-tls.yml                    # PostgreSQL + Elasticsearch + TLS
docker-compose-postgres.yml               # PostgreSQL only (minimal)
docker-compose-cass-es.yml                # Cassandra + Elasticsearch
docker-compose-mysql.yml                  # MySQL only
docker-compose-mysql-es.yml               # MySQL + Elasticsearch
docker-compose-postgres-opensearch.yml    # PostgreSQL + OpenSearch
docker-compose-multirole.yaml             # Multi-role server + Prometheus + Grafana
```

**How it works:**
- Each file is self-contained -- `docker compose -f docker-compose-postgres.yml up`
- No inheritance, no includes, no profiles
- Files share the same `temporalio/auto-setup` image but wire different databases

**Pros:** Dead simple, each file is obvious, no compose knowledge needed.
**Cons:** Massive duplication across files; changes to a shared service must be replicated 8 times.

**Verdict:** Good for dimension-switching (different databases), bad for optional component toggling.

---

### Pattern B: Base + Override Files (Supabase, Mattermost)

**Project:** [Supabase](https://github.com/supabase/supabase/tree/master/docker)

Supabase uses a **single large base file** with variant overrides:

```
docker/
  docker-compose.yml            # Base: all 12+ services (Kong, Auth, Realtime, Storage, etc.)
  docker-compose.caddy.yml      # Override: swap reverse proxy to Caddy
  docker-compose.nginx.yml      # Override: swap reverse proxy to Nginx
  docker-compose.s3.yml         # Override: add S3-backed storage
  docker-compose.rustfs.yml     # Override: use Rust-based storage
  dev/
    docker-compose.dev.yml      # Override: add mail testing, live-reload, ephemeral volumes
  .env.example
  volumes/                      # Volume configs and init scripts
  tests/
```

**How it works:**
```bash
# Production (base only)
docker compose up

# Development
docker compose -f docker-compose.yml -f ./dev/docker-compose.dev.yml up

# With S3 storage
docker compose -f docker-compose.yml -f docker-compose.s3.yml up
```

**Key insight:** Supabase does NOT use profiles. They document "if you don't need Logflare, Realtime, Storage, or Edge Runtime, remove the corresponding sections from docker-compose.yml." This is a manual editing approach for optional services.

---

**Project:** [Mattermost](https://github.com/mattermost/docker)

Mattermost uses the same overlay pattern but for a different axis (infrastructure variants):

```
docker-compose.yml                  # Base: app + database
docker-compose.nginx.yml            # Add: nginx reverse proxy
docker-compose.without-nginx.yml    # Alternative: no reverse proxy
```

```bash
# Without nginx
docker compose -f docker-compose.yml -f docker-compose.without-nginx.yml up -d
```

---

### Pattern C: Profiles (n8n AI Starter Kit)

**Project:** [n8n Self-Hosted AI Starter Kit](https://github.com/n8n-io/self-hosted-ai-starter-kit)

n8n uses **Docker Compose profiles** to handle hardware-variant deployments:

```yaml
services:
  # Always-on services (no profile = always starts)
  n8n:
    image: n8nio/n8n
    # ...

  qdrant:
    image: qdrant/qdrant
    # ...

  # Profile-gated services
  ollama-cpu:
    <<: *service-ollama
    profiles: [cpu]

  ollama-gpu:
    <<: *service-ollama
    profiles: [gpu-nvidia]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  ollama-gpu-amd:
    <<: *service-ollama
    image: ollama/ollama:rocm
    profiles: [gpu-amd]
    devices:
      - "/dev/kfd"
      - "/dev/dri"
```

**Usage:**
```bash
docker compose --profile gpu-nvidia up    # NVIDIA GPU mode
docker compose --profile cpu up           # CPU-only mode
docker compose --profile gpu-amd up       # AMD GPU mode
```

**Key insight:** YAML anchors (`<<: *service-ollama`) deduplicate shared config between profile variants. Unassigned services always start.

---

### Pattern D: Include Directive (Appwrite)

**Project:** [Appwrite](https://github.com/appwrite/appwrite/pull/9621) (migrated in 2024)

Appwrite split a monolithic `docker-compose.yml` into **30+ modular files** using the `include` directive:

```
compose.yml                              # Root: includes all modules
compose.override.yml                     # Dev overrides
compose/
  appwrite.yml                           # Core application
  traefik.yml                            # Reverse proxy
  mariadb.yml                            # Database
  redis.yml                              # Cache
  realtime.yml                           # WebSocket service
  console.yml                            # Admin console
  openruntimes-executor.yml              # Function runtime
  openruntimes-proxy.yml                 # Runtime proxy
  task-scheduler-executions.yml          # Scheduled tasks
  task-scheduler-functions.yml
  task-scheduler-messages.yml
  task-stats-resources.yml
  worker-audits.yml                      # Workers (16 total)
  worker-builds.yml
  worker-certificates.yml
  worker-databases.yml
  worker-deletions.yml
  worker-functions.yml
  worker-mails.yml
  worker-maintenance.yml
  worker-messaging.yml
  worker-migrations.yml
  worker-stats.yml
  worker-webhooks.yml
```

**Root compose.yml:**
```yaml
include:
  - compose/appwrite.yml
  - compose/traefik.yml
  - compose/mariadb.yml
  - compose/redis.yml
  - compose/realtime.yml
  - compose/console.yml
  # ... all other modules
```

**Include with env_file and project_directory:**
```yaml
include:
  - path: config/ckan/ckan.yaml
    env_file:
      - config/ckan/.env
      - config/.global-env
    project_directory: .
```

**Key insight:** `include` (Compose v2.20+, stable in v2.24+) loads each file as its own project with its own path resolution. Conflicts between included files are errors, not merges. Recursive includes are supported.

---

### Pattern E: Install Script + Env Toggles (Sentry)

**Project:** [Sentry Self-Hosted](https://github.com/getsentry/self-hosted)

Sentry uses a single `docker-compose.yml` but wraps it with an `install.sh` script that handles setup:

```
docker-compose.yml                # All services
install.sh                        # Setup wizard: generates .env, runs migrations
.env                              # Generated by install.sh
.env.custom                       # User overrides (takes precedence)
sentry/
  sentry.conf.py                  # Advanced Python config
  enhance-image.sh                # Custom image modifications
optional-modifications/           # Directory for user-added extensions
```

**Key insight:** Sentry uses a script-driven approach where `install.sh` generates config. Optional features (pre-GA) are enabled via feature flags in `sentry.conf.py`, not via compose profiles. The `optional-modifications/` directory is a convention for user extensions.

---

## 2. Docker Compose Profiles vs Multiple Files vs Extends vs Include

### Comparison Matrix

| Feature | Profiles | Multiple Files (-f) | Extends | Include |
|---------|----------|-------------------|---------|---------|
| **Min version** | Compose v2.1 | Always | Compose v2.21 | Compose v2.20 (stable v2.24) |
| **Granularity** | Per-service | Per-file | Per-service | Per-file |
| **Use case** | Toggle optional services | Environment overlays | Share base configs | Modular decomposition |
| **Complexity** | Low | Medium | Medium | Medium-High |
| **Duplication** | None (single file) | Low (overlay only) | None (inheritance) | None (modular) |
| **Combinability** | `--profile a --profile b` | `-f a.yml -f b.yml` | Within file | Recursive |
| **Conflict handling** | N/A (single file) | Merge (last wins) | Merge | Error on conflict |
| **Path resolution** | Same file | Per-file context | Tricky (known issue) | Per-file (solved) |

### When to Use What

**Profiles** -- Best for:
- Toggling optional services ON/OFF within a single deployment mode
- Hardware variants (cpu/gpu, local-db/remote-db)
- Debug/monitoring tools that are sometimes needed
- Small number of variants (2-5 profiles)

**Multiple Files (-f)** -- Best for:
- Environment overlays (dev/staging/prod)
- Infrastructure swaps (nginx/caddy, postgres/mysql)
- Adding entirely new service groups
- When you need to REPLACE config, not just toggle

**Include** -- Best for:
- Large monorepos with 10+ services
- Team ownership boundaries (each team owns their compose module)
- When services have their own .env files
- Recursive composition (module A includes module B)

**Extends** -- Best for:
- DRY-ing shared config between similar services
- Base service templates (all workers share health checks)
- Cross-project config sharing

### Modern Best Practice (2025-2026)

The evolution has been: **separate files -> profiles -> include**

The Docker team recommends `include` for large projects (see [Docker blog: Improve Docker Compose Modularity with Include](https://www.docker.com/blog/improve-docker-compose-modularity-with-include/)). For smaller projects with optional services, **profiles remain the simplest and most widely adopted approach**.

**Emerging pattern:** Combine profiles + include:
```yaml
# compose.yml
include:
  - infra/compose.yml        # Redis, Postgres, MinIO
  - services/compose.yml     # Core services (always on)
  - monitoring/compose.yml   # Grafana, Prometheus (profile: monitoring)
```

Each included file can assign its own profiles, giving you modularity + toggleability.

---

## 3. Deploy Directory Structure Patterns

### Pattern 1: Flat Deploy Directory (Supabase, Appsmith)

```
deploy/
  docker/
    docker-compose.yml
    docker-compose.s3.yml
    .env.example
    volumes/
```
- Simple, flat structure
- Variants as sibling compose files
- Used by projects with one main deployment mode + optional overlays

### Pattern 2: Mode-Based Subdirectories (Our Current + Temporal)

```
deploy/
  compose/                    # Full stack (Docker Compose)
    docker-compose.yml
    docker-compose.local-db.yml
    Makefile
  lite/                       # Single-machine (Dockerfile + supervisord)
    Dockerfile.lite
    Makefile
  helm/                       # Kubernetes
    Chart.yaml
    values.yaml
    templates/
  env/
    env-example
  scripts/
```
- Each deployment MODE gets its own directory
- Clear separation between compose, Helm, single-binary
- **This is what we already have** -- proven pattern

### Pattern 3: Service-Based Subdirectories (Appwrite Post-Migration)

```
compose/
  appwrite.yml
  mariadb.yml
  redis.yml
  traefik.yml
  worker-*.yml
compose.yml          # Root include file
compose.override.yml # Dev overrides
```
- Each service gets its own file
- Root compose.yml uses `include:` to assemble
- Good for 10+ services with team ownership boundaries

### Pattern 4: Sentry's Script-Driven Approach

```
docker-compose.yml
install.sh
.env / .env.custom
sentry/
  sentry.conf.py
  enhance-image.sh
optional-modifications/
_integration-test/
_unit-test/
```
- Single compose file, complexity managed by install script
- Optional features via config files, not compose variants
- Tests in dedicated top-level directories

### Recommendation for Vexa

**Hybrid of Pattern 2 + Pattern 3:** Keep the mode-based `deploy/` structure, but use `include` within `deploy/compose/` to modularize:

```
deploy/
  compose/
    docker-compose.yml              # Root: includes base modules
    docker-compose.local-db.yml     # Overlay: add local Postgres
    modules/
      infra.yml                     # Redis, MinIO
      runtime-api.yml               # runtime-api service
      agent-api.yml             # agent-api service
      meeting.yml                   # meeting-api + transcription-collector + tts
      admin.yml                     # admin-api + api-gateway
      frontend.yml                  # dashboard
      mcp.yml                       # MCP service
    Makefile
  lite/
    Dockerfile.lite
    Makefile
  helm/
    Chart.yaml
    values.yaml
    charts/                         # Subcharts per component
```

---

## 4. Testing Each Composition

### Pattern A: CI Matrix (GitHub Actions)

Test each composition as a separate matrix entry:

```yaml
# .github/workflows/compositions.yml
name: Test Compositions
on: [push, pull_request]

jobs:
  smoke-test:
    strategy:
      matrix:
        composition:
          - runtime-only
          - runtime-agent
          - runtime-meeting
          - full-stack
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Start composition
        run: make up-${{ matrix.composition }}
      - name: Wait for health
        run: make wait-healthy TIMEOUT=60
      - name: Smoke test
        run: make smoke-${{ matrix.composition }}
      - name: Teardown
        if: always()
        run: make down
```

**Used by:** Most serious open-source projects. The [peter-evans/docker-compose-actions-workflow](https://github.com/peter-evans/docker-compose-actions-workflow) repository is a canonical example.

### Pattern B: Per-Composition Smoke Script

Each composition gets a minimal smoke test that verifies:
1. All containers start and reach healthy state
2. Key endpoints respond (health checks)
3. Services can communicate (e.g., API can reach database)

```bash
#!/bin/bash
# smoke-tests/runtime-only.sh
set -e

echo "Testing runtime-api standalone..."
curl -sf http://localhost:8090/health || exit 1
echo "PASS: runtime-api health"

# Verify Redis connectivity
docker compose exec runtime-api python -c "import redis; redis.from_url('redis://redis:6379').ping()" || exit 1
echo "PASS: runtime-api -> Redis"

echo "All smoke tests passed for runtime-only composition"
```

### Pattern C: Docker Compose Health Checks as Gate

Rely on Docker Compose's built-in health checks and `--wait` flag:

```bash
# Start and wait for all services to be healthy
docker compose --profile runtime-only up -d --wait --wait-timeout 120

# If --wait succeeds, all health checks passed
echo "Composition is healthy"

# Then run application-level smoke tests
./smoke-tests/runtime-only.sh
```

### Pattern D: Sentry's Integration Test Directory

Sentry maintains dedicated test directories:
```
_integration-test/    # Full stack integration tests
_unit-test/           # Component-level tests
```

### Recommendation for Vexa

Use **Pattern A (CI Matrix) + Pattern B (Per-Composition Smoke Scripts)**:

```
tests/
  compositions/
    smoke-runtime-only.sh
    smoke-runtime-agent.sh
    smoke-runtime-meeting.sh
    smoke-full-stack.sh
    smoke-lite.sh
```

Each script:
1. Calls `make up-<composition>`
2. Waits for health checks
3. Hits key endpoints
4. Verifies inter-service connectivity
5. Always calls `make down`

---

## 5. Helm Chart Patterns for Optional Components

### Pattern A: Umbrella Chart with Conditional Dependencies (Grafana LGTM, Temporal)

**Project:** [Grafana LGTM-Distributed](https://github.com/grafana/helm-charts/tree/main/charts/lgtm-distributed)

`Chart.yaml`:
```yaml
dependencies:
  - name: grafana
    version: "^10.0.0"
    repository: https://grafana.github.io/helm-charts
    condition: grafana.enabled
  - name: loki-distributed
    alias: loki
    version: "^0.80.5"
    repository: https://grafana.github.io/helm-charts
    condition: loki.enabled
  - name: mimir-distributed
    alias: mimir
    version: "^5.8.0"
    repository: https://grafana.github.io/helm-charts
    condition: mimir.enabled
  - name: tempo-distributed
    alias: tempo
    version: "^1.48.0"
    repository: https://grafana.github.io/helm-charts
    condition: tempo.enabled
```

`values.yaml`:
```yaml
grafana:
  enabled: true
loki:
  enabled: true
mimir:
  enabled: true
tempo:
  enabled: false    # Disabled by default
```

**Project:** [Temporal](https://github.com/temporalio/helm-charts)

`Chart.yaml` dependencies:
```yaml
dependencies:
  - name: cassandra
    version: "0.14.3"
    condition: cassandra.enabled
  - name: prometheus
    version: "25.22.0"
    condition: prometheus.enabled
  - name: elasticsearch
    version: "7.17.3"
    condition: elasticsearch.enabled
  - name: grafana
    version: "8.0.2"
    condition: grafana.enabled
```

**Three deployment patterns Temporal supports:**
1. **Batteries included** -- all deps enabled (default)
2. **Minimal dev** -- `server.replicaCount=1`, all monitoring off
3. **BYO dependencies** -- disable bundled Cassandra/ES, configure external connections

### Pattern B: Tags for Component Groups

```yaml
# Chart.yaml
dependencies:
  - name: frontend
    tags: [frontend]
  - name: backend
    tags: [backend]
  - name: worker
    tags: [backend]
  - name: monitoring
    tags: [observability]
```

```bash
# Install only frontend components
helm install my-app . --set tags.frontend=true --set tags.backend=false

# Install everything except observability
helm install my-app . --set tags.observability=false
```

### Pattern C: Environment-Specific Values Files

```
helm/
  Chart.yaml
  values.yaml                    # Base/defaults
  values-dev.yaml                # Development overrides
  values-staging.yaml            # Staging overrides
  values-production.yaml         # Production overrides
  values-runtime-only.yaml       # runtime-api standalone
  values-meeting.yaml            # runtime-api + meeting-api
  values-full.yaml               # Everything enabled
```

```bash
# Deploy runtime-only
helm install vexa . -f values-runtime-only.yaml

# Deploy full stack in production
helm install vexa . -f values-production.yaml -f values-full.yaml
```

### Recommendation for Vexa

Use **Pattern A (Conditional Dependencies) + Pattern C (Values Files)**:

```yaml
# deploy/helm/Chart.yaml
dependencies:
  - name: runtime-api
    version: "1.x.x"
    repository: file://charts/runtime-api
    # Always required, no condition

  - name: agent-api
    version: "1.x.x"
    repository: file://charts/agent-api
    condition: agent-api.enabled

  - name: meeting-api
    version: "1.x.x"
    repository: file://charts/meeting-api
    condition: meeting-api.enabled

  - name: admin-api
    version: "1.x.x"
    repository: file://charts/admin-api
    condition: admin-api.enabled

  # ... etc for each service

# deploy/helm/values-runtime-only.yaml
agent-api:
  enabled: false
meeting-api:
  enabled: false
admin-api:
  enabled: false
# ...only runtime-api + redis
```

---

## 6. Makefile / CLI Patterns

### Pattern A: Simple Target-Per-Composition

The most common pattern across open-source projects:

```makefile
# Makefile
.PHONY: up-runtime up-agent up-meeting up-full up-lite down

COMPOSE_CMD = docker compose --env-file .env

up-runtime:
	$(COMPOSE_CMD) --profile runtime up -d

up-agent:
	$(COMPOSE_CMD) --profile runtime --profile agent up -d

up-meeting:
	$(COMPOSE_CMD) --profile runtime --profile meeting up -d

up-full:
	$(COMPOSE_CMD) up -d

up-lite:
	cd deploy/lite && $(MAKE) build && docker run -d ...

down:
	$(COMPOSE_CMD) down
```

### Pattern B: Dynamic Service Selection (Advanced)

From [dev.to/marrouchi](https://dev.to/marrouchi/dynamically-start-docker-compose-services-with-a-simple-makefile-2ecb):

```makefile
FOLDER := ./deploy/compose
SERVICES := monitoring agent-api meeting-api

# Dynamically include compose files based on env vars
define compose_files
  $(foreach service,$(SERVICES),\
    $(if $($(shell echo $(service) | tr a-z- A-Z_)),\
      -f $(FOLDER)/modules/$(service).yml))
endef

dev: check-env
	docker compose -f $(FOLDER)/docker-compose.yml \
	  $(call compose_files) up -d

# Usage: make dev MONITORING=1 AGENT_RUNTIME=1
```

### Pattern C: CLI Wrapper (Sentry-style)

```bash
#!/bin/bash
# scripts/vexa-up.sh
COMPOSITION=${1:-full}

case $COMPOSITION in
  runtime)
    docker compose --profile runtime up -d
    ;;
  agent)
    docker compose --profile runtime --profile agent up -d
    ;;
  meeting)
    docker compose --profile runtime --profile meeting up -d
    ;;
  full)
    docker compose up -d
    ;;
  lite)
    cd deploy/lite && make build && docker run -d ...
    ;;
  *)
    echo "Usage: $0 {runtime|agent|meeting|full|lite}"
    exit 1
    ;;
esac
```

### Recommendation for Vexa

**Pattern A (Simple Targets)** -- straightforward, discoverable, self-documenting:

```makefile
# Top-level Makefile additions
up-runtime:    ## Start runtime-api only (container orchestration)
	$(COMPOSE_CMD) --profile runtime up -d

up-agent:      ## Start runtime-api + agent-api (AI agents)
	$(COMPOSE_CMD) --profile runtime --profile agent up -d

up-meeting:    ## Start runtime-api + meeting services
	$(COMPOSE_CMD) --profile runtime --profile meeting up -d

up:            ## Start full stack (default)
	$(COMPOSE_CMD) up -d

help:          ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | sort | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
```

---

## 7. Recommendation for Vexa

### Chosen Approach: Profiles + Include (Hybrid)

Based on the research, here's the recommended architecture:

#### Composition Strategy

| Approach | Why |
|----------|-----|
| **Profiles** for toggling service groups | Proven, simple, single-file readable |
| **Include** for modularizing large compose file | Clean ownership boundaries, each module has own .env |
| **Multiple files** for infra overlays | Keep local-db overlay separate (already working) |
| **Makefile targets** for user interface | `make up-runtime`, `make up-meeting`, `make up` |
| **CI matrix** for testing compositions | One smoke test per composition |

#### Profile Design

```
Profile: runtime    -> runtime-api, redis
Profile: agent      -> agent-api (requires: runtime)
Profile: meeting    -> meeting-api, transcription-collector, tts-service (requires: runtime)
Profile: admin      -> admin-api, api-gateway (requires: runtime)
Profile: frontend   -> dashboard (requires: admin)
Profile: mcp        -> mcp service (requires: admin)
No profile          -> all services start (full stack)
```

#### File Structure

```
deploy/
  compose/
    docker-compose.yml              # Root compose, uses include + profiles
    docker-compose.local-db.yml     # Overlay: local Postgres
    modules/                        # Optional: split into modules if >500 lines
      infra.yml                     # Redis, MinIO
      runtime.yml                   # runtime-api
      agent.yml                     # agent-api
      meeting.yml                   # meeting-api, transcription-collector, tts
      admin.yml                     # admin-api, api-gateway
      frontend.yml                  # dashboard
    Makefile
  lite/
    Dockerfile.lite
    Makefile
  helm/
    Chart.yaml                      # Umbrella chart with conditional deps
    values.yaml                     # Full stack defaults
    values-runtime-only.yaml        # runtime-api standalone
    values-runtime-agent.yaml       # runtime + agent
    values-runtime-meeting.yaml     # runtime + meeting
    charts/                         # Local subcharts
  env/
    env-example
  scripts/
tests/
  compositions/
    smoke-runtime-only.sh
    smoke-runtime-agent.sh
    smoke-runtime-meeting.sh
    smoke-full-stack.sh
```

#### Key Decisions

1. **Start with profiles in a single compose file.** Only split into `include` modules when the file exceeds ~500 lines or team ownership boundaries emerge. We're at ~340 lines now -- close but not there yet.

2. **Services without a profile always start.** Redis and infrastructure are always needed. Profile-gated services only start when activated.

3. **One Makefile target per composition.** Users never need to know about profiles -- they just run `make up-runtime`.

4. **One smoke test per composition.** CI matrix tests each composition independently.

5. **Helm mirrors compose profiles.** Each `values-<composition>.yaml` toggles the same services as the corresponding profile.

---

## 8. The N×M Matrix Problem: Compositions × Backends

> Added after team lead feedback: each composition also has 3 deployment backends (Process, Docker, Kubernetes), creating a matrix of configs.

### The Problem

With 5 compositions × 3 backends = 15 potential config files:

| | Process (supervisord) | Docker (compose) | Kubernetes (helm) |
|---|---|---|---|
| **runtime-api standalone** | supervisord.runtime.conf | compose --profile runtime | values-runtime-only.yaml |
| **runtime + agent** | supervisord.agent.conf | compose --profile runtime,agent | values-runtime-agent.yaml |
| **runtime + meeting** | supervisord.meeting.conf | compose --profile runtime,meeting | values-runtime-meeting.yaml |
| **full stack** | supervisord.conf | compose up (all) | values.yaml |
| **Vexa Lite** | supervisord.conf (same) | N/A (single container) | values-lite.yaml (single pod) |

That's 13 distinct configs. How do projects avoid this explosion?

### How Projects Handle This

#### Strategy 1: Separate Repos/Charts Per Backend (Most Common)

Most projects maintain **independent configs per backend** and accept some duplication:

**Sentry:**
- Docker Compose: [getsentry/self-hosted](https://github.com/getsentry/self-hosted) (official, maintained by Sentry)
- Helm: [sentry-kubernetes/charts](https://github.com/sentry-kubernetes/charts) (community, separate repo)
- No process mode
- **No shared source of truth** -- Helm chart is community-maintained, often lags behind

**Supabase:**
- Docker Compose: [supabase/supabase/docker/](https://github.com/supabase/supabase/tree/master/docker) (official)
- Helm: [supabase-community/supabase-kubernetes](https://github.com/supabase-community/supabase-kubernetes) (community)
- **Parallel maintenance** -- Helm chart mirrors compose services but no automated sync

**Mattermost:**
- Docker Compose: [mattermost/docker](https://github.com/mattermost/docker)
- Helm: [mattermost/mattermost-helm](https://github.com/mattermost/mattermost-helm)
- Operator: [mattermost/mattermost-operator](https://github.com/mattermost/mattermost-operator)
- **Three separate repos** -- each maintained independently

**PostHog:**
- Docker Compose: archived `PostHog/deployment` repo (`/compose` directory)
- Helm: [PostHog/charts-clickhouse](https://github.com/PostHog/charts-clickhouse)
- Eventually **sunset Helm** entirely due to maintenance burden

**Key insight:** Most projects find that maintaining Helm + Compose in sync is expensive. Many have community-maintained Helm charts that lag behind. PostHog even abandoned Helm support because the maintenance cost was too high for the user base.

#### Strategy 2: Shared Container Image, Different Orchestration (Temporal)

**Temporal** uses the same binary/image across all backends but different wrappers:

```
temporal CLI (single binary)     → temporal server start-dev
                                   (all 4 services in one process)

Docker Compose (8 files)         → temporalio/auto-setup image
                                   (same binary, different DB wiring)

Helm Chart (umbrella)            → temporalio/server image
                                   (same binary, 4 separate pods)
```

The **shared source of truth** is the `temporalio/server` binary and its YAML config format. All backends configure the same knobs, just expressed differently (env vars in compose, values.yaml in Helm, CLI flags in single-binary mode).

**Composition in each backend:**
- Single binary: `temporal server start-dev` (always all-in-one)
- Docker Compose: choose a file (`docker-compose-postgres.yml` vs `docker-compose-mysql-es.yml`)
- Helm: toggle dependencies in values.yaml (`cassandra.enabled: false`, `elasticsearch.enabled: false`)

**This is the closest model to what Vexa needs.**

#### Strategy 3: Converter Tools (Katenary, Kompose)

Tools that generate one format from another:

**[Katenary](https://github.com/Katenary/katenary)** -- Docker Compose → Helm Chart:
- Reads `compose.yaml`, generates a full Helm chart with templates, values.yaml, Chart.yaml
- Uses labels in compose files to control conversion behavior
- **Single source of truth is the compose file**
- One-time generation or CI-integrated re-generation

**[Kompose](https://kubernetes.io/docs/tasks/configure-pod-container/translate-compose-kubernetes/)** -- Docker Compose → K8s manifests:
- Official Kubernetes SIG tool
- Converts compose services to K8s Deployments + Services
- Less feature-rich than Katenary for Helm

**Verdict:** Appealing in theory but fragile in practice. Real Helm charts need features (RBAC, PVCs, Ingress, HPA) that don't exist in compose files. Most projects that tried converters ended up maintaining hand-written Helm charts anyway.

#### Strategy 4: Kustomize Components (Best for K8s N×M)

**[Kustomize components](https://github.com/kubernetes-sigs/kustomize/blob/master/examples/components.md)** solve exactly the N×M problem for Kubernetes:

```
k8s/
  base/                          # All services, minimal config
    deployment-runtime-api.yaml
    deployment-meeting-api.yaml
    deployment-admin-api.yaml
    ...
    kustomization.yaml

  components/                    # Optional features (M dimension)
    external-db/
      kustomization.yaml         # kind: Component
      configmap.yaml
    tls/
      kustomization.yaml
    monitoring/
      kustomization.yaml

  overlays/                      # Compositions (N dimension)
    runtime-only/
      kustomization.yaml         # resources: [../../base], patches to disable non-runtime
    runtime-meeting/
      kustomization.yaml
    full-stack/
      kustomization.yaml
      components:
        - ../../components/monitoring
        - ../../components/tls
```

Each overlay selects which services from base and which optional components to include. N compositions + M optional features = N overlay files + M component directories (NOT N×M files).

**Used by:** ArgoCD, Cilium, many CNCF projects for their own internal deployment configs.

#### Strategy 5: Umbrella Helm Chart with Values Files (Best for Helm N dimension)

**GitLab** is the gold standard here -- 13+ subcharts, all toggleable:

```yaml
# GitLab Chart.yaml structure
dependencies:
  - name: gitlab           # Core (webservice, sidekiq, gitaly, shell, etc.)
  - name: nginx-ingress    # condition: nginx-ingress.enabled
  - name: postgresql       # condition: postgresql.enabled
  - name: redis            # condition: redis.enabled
  - name: minio            # condition: global.minio.enabled
  - name: registry         # condition: registry.enabled
  - name: prometheus       # condition: prometheus.enabled
  - name: certmanager      # condition: certmanager.install
```

**Composition via values files:**
```bash
# GitLab minimal (no monitoring, no registry, external DB)
helm install gitlab . \
  -f values-minimal.yaml \
  --set postgresql.enabled=false \
  --set prometheus.enabled=false \
  --set registry.enabled=false

# GitLab full (everything)
helm install gitlab . -f values.yaml
```

**Grafana LGTM-Distributed** uses the same pattern:
```yaml
# Chart.yaml
dependencies:
  - name: grafana
    condition: grafana.enabled
  - name: loki-distributed
    alias: loki
    condition: loki.enabled
  - name: mimir-distributed
    alias: mimir
    condition: mimir.enabled
  - name: tempo-distributed
    alias: tempo
    condition: tempo.enabled
```

### How to Avoid the N×M Explosion

Based on all research, projects use **3 key strategies** to collapse the matrix:

#### 1. Shared Service Registry (Source of Truth)

Define each service ONCE with its properties, then generate/reference from each backend:

```yaml
# services.yaml (the ONE source of truth)
services:
  runtime-api:
    port: 8090
    depends_on: [redis]
    profiles: [runtime]        # which compositions include this
    health_check: /health
    env:
      REDIS_URL: "{{redis_url}}"

  meeting-api:
    port: 8080
    depends_on: [redis, runtime-api, postgres]
    profiles: [meeting]
    health_check: /health
    env:
      REDIS_URL: "{{redis_url}}"
      RUNTIME_API_URL: "{{runtime_api_url}}"
```

No project we found does this formally with code generation, but the pattern is implicit in how Temporal shares a single config schema across all backends.

#### 2. Composition = Toggle (Same Config, Different Booleans)

Instead of N separate config files per backend, use ONE config file per backend with boolean toggles:

| Backend | How compositions are expressed |
|---------|-------------------------------|
| **Docker Compose** | `--profile runtime --profile meeting` (booleans via CLI) |
| **Helm** | `meeting-api.enabled: true` in values.yaml (booleans via YAML) |
| **Supervisord** | `autostart=%(ENV_ENABLE_MEETING)s` (booleans via env vars) |
| **Kustomize** | `components: [../../components/meeting]` (booleans via file inclusion) |

This collapses N×M → M config files (one per backend) + N preset files that set the right booleans.

#### 3. Preset Files Per Composition

Each composition is a thin preset that sets booleans for a backend:

```
deploy/
  presets/
    runtime-only.env           # ENABLE_MEETING=false, ENABLE_AGENT=false, ...
    runtime-agent.env          # ENABLE_MEETING=false, ENABLE_AGENT=true, ...
    runtime-meeting.env        # ENABLE_MEETING=true, ENABLE_AGENT=false, ...
    full-stack.env             # ENABLE_MEETING=true, ENABLE_AGENT=true, ...
  compose/
    docker-compose.yml         # All services, profiles gated by ENABLE_*
  helm/
    values.yaml                # All services, .enabled flags
    values-runtime-only.yaml   # meetingApi.enabled: false, agentRuntime.enabled: false
    values-runtime-meeting.yaml
    values-full.yaml
  lite/
    supervisord.conf           # All services, autostart gated by ENABLE_*
```

**Total config files:** 4 presets + 1 compose + 4 helm values + 1 supervisord = **10 files** (vs 15 for naive N×M).

### What Vexa Already Has (Good News)

Looking at our current codebase:

1. **Helm chart already has `enabled: true/false` per component** -- `apiGateway.enabled`, `adminApi.enabled`, `botManager.enabled`, `transcriptionCollector.enabled`, `mcp.enabled`, `dashboard.enabled`, `postgres.enabled`, `redis.enabled`. This is exactly the GitLab/Grafana pattern. We just need values-files per composition.

2. **Supervisord.conf already lists all services** -- we just need to make `autostart` conditional on env vars.

3. **Docker Compose is a single file** -- we just need to add `profiles:` to each service.

### Recommended Architecture for Vexa

```
deploy/
  presets/                                # Composition definitions (shared across backends)
    runtime-only.env                      # Booleans for runtime-only
    runtime-agent.env
    runtime-meeting.env
    full-stack.env

  compose/
    docker-compose.yml                    # Single file, profiles per service
    docker-compose.local-db.yml           # Overlay: local Postgres
    Makefile                              # make up-runtime, make up-meeting, etc.

  helm/
    charts/vexa/
      Chart.yaml                          # Already has all components
      values.yaml                         # Full stack defaults (already exists)
      values-runtime-only.yaml            # meeting/admin/dashboard disabled
      values-runtime-agent.yaml           # meeting/admin disabled, agent enabled
      values-runtime-meeting.yaml         # agent disabled, meeting enabled

  lite/
    supervisord.conf                      # All services, autostart=%(ENV_ENABLE_*)s
    Dockerfile.lite
    Makefile
```

**Config count:** 4 presets + 1 compose + 1 compose-overlay + 4 helm-values + 1 supervisord = **11 files total** for 5 compositions × 3 backends. The presets are reusable: `make up-runtime` reads `presets/runtime-only.env` whether calling compose or supervisord.

**The key insight:** Compositions are just named sets of booleans. Express them as booleans in every backend, and store the named sets in shared preset files.

---

### Projects That Ship Both Compose AND Helm

| Project | Compose | Helm | Shared Source? | Notes |
|---------|---------|------|----------------|-------|
| **Temporal** | [temporalio/docker-compose](https://github.com/temporalio/docker-compose) | [temporalio/helm-charts](https://github.com/temporalio/helm-charts) | Same binary + config schema | Closest to shared source of truth |
| **GitLab** | Single docker image (Omnibus) | [gitlab-org/charts/gitlab](https://gitlab.com/gitlab-org/charts/gitlab) | Same application | Omnibus = all-in-one; Helm = distributed |
| **Supabase** | [docker/](https://github.com/supabase/supabase/tree/master/docker) | [supabase-community/supabase-kubernetes](https://github.com/supabase-community/supabase-kubernetes) | No | Community Helm, lags behind |
| **Sentry** | [getsentry/self-hosted](https://github.com/getsentry/self-hosted) | [sentry-kubernetes/charts](https://github.com/sentry-kubernetes/charts) | No | Community Helm, separate repo |
| **Mattermost** | [mattermost/docker](https://github.com/mattermost/docker) | [mattermost/mattermost-helm](https://github.com/mattermost/mattermost-helm) | No | Three separate repos |
| **n8n** | Official docs | [8gears/n8n-helm-chart](https://github.com/8gears/n8n-helm-chart) | No | Community Helm chart |
| **PostHog** | Archived | [PostHog/charts-clickhouse](https://github.com/PostHog/charts-clickhouse) | No | **Sunset Helm** -- too expensive to maintain |

**Conclusion:** No project fully solves compose↔helm sync. The pragmatic approach is:
1. Compose for dev/single-machine (simpler, faster iteration)
2. Helm for K8s production (more features needed: RBAC, PVC, HPA)
3. Accept that they drift and maintain them independently
4. Share the same container images and env var contract

---

## Sources

### Docker Compose
- [Docker Docs: Use multiple Compose files](https://docs.docker.com/compose/how-tos/multiple-compose-files/)
- [Docker Docs: Include directive](https://docs.docker.com/compose/how-tos/multiple-compose-files/include/)
- [Docker Docs: Profiles](https://docs.docker.com/compose/how-tos/profiles/)
- [Docker Docs: Extends](https://docs.docker.com/compose/how-tos/multiple-compose-files/extends/)
- [Docker Blog: Improve Docker Compose Modularity with Include](https://www.docker.com/blog/improve-docker-compose-modularity-with-include/)
- [Keitaro: Streamline Docker Compose with the Include Directive](https://www.keitaro.com/insights/2025/01/13/streamline-docker-compose-with-the-include-directive/)

### Real-World Projects
- [Temporal docker-compose (8 files)](https://github.com/temporalio/docker-compose)
- [Temporal Helm charts](https://github.com/temporalio/helm-charts)
- [Supabase docker/ directory](https://github.com/supabase/supabase/tree/master/docker)
- [Supabase Docker Compose Architecture (DeepWiki)](https://deepwiki.com/supabase/supabase/3.1-docker-compose-architecture)
- [Mattermost Docker](https://github.com/mattermost/docker)
- [Sentry Self-Hosted](https://github.com/getsentry/self-hosted)
- [Appwrite: Split Docker Compose PR #9621](https://github.com/appwrite/appwrite/pull/9621)
- [n8n Self-Hosted AI Starter Kit (profiles)](https://github.com/n8n-io/self-hosted-ai-starter-kit)
- [Plausible Community Edition](https://github.com/plausible/community-edition)
- [Grafana LGTM-Distributed Helm Chart](https://github.com/grafana/helm-charts/tree/main/charts/lgtm-distributed)

### CI/CD
- [GitHub Actions Docker Compose Workflow](https://github.com/peter-evans/docker-compose-actions-workflow)
- [Docker Compose with Tests Action](https://github.com/marketplace/actions/docker-compose-with-tests-action)

### Makefile Patterns
- [Dynamic Docker Compose Toggling with Makefile](https://dev.to/marrouchi/dynamically-start-docker-compose-services-with-a-simple-makefile-2ecb)
- [Simplifying Docker Compose with Makefile](https://medium.com/freestoneinfotech/simplifying-docker-compose-operations-using-makefile-26d451456d63)

### Helm Charts
- [Helm: Subcharts and Global Values](https://helm.sh/docs/chart_template_guide/subcharts_and_globals/)
- [Helm Umbrella Charts: Managing Multi-Service Applications](https://oneuptime.com/blog/post/2026-01-17-helm-umbrella-charts-multi-service/view)
- [Helm Conditions and Tagging for Umbrella Charts](https://faun.pub/helm-conditions-and-tagging-for-umbrella-charts-f0ca9f6bb499)
- [GitLab Helm Chart Subcharts](https://docs.gitlab.com/charts/charts/gitlab/)
- [GitLab Helm Chart Globals](https://docs.gitlab.com/charts/charts/globals/)

### N×M Matrix / Multi-Backend
- [Kustomize Components (optional features)](https://github.com/kubernetes-sigs/kustomize/blob/master/examples/components.md)
- [Kustomize Multibases](https://github.com/kubernetes-sigs/kustomize/blob/master/examples/multibases/README.md)
- [Katenary: Docker Compose → Helm Chart Converter](https://github.com/Katenary/katenary)
- [Kompose: Docker Compose → K8s Resources](https://kubernetes.io/docs/tasks/configure-pod-container/translate-compose-kubernetes/)
- [Temporal Self-Hosted Deployment Guide](https://docs.temporal.io/self-hosted-guide/deployment)
- [Temporal: All the Ways to Run a Cluster](https://docs.temporal.io/kb/all-the-ways-to-run-a-cluster)
- [PostHog: Sunsetting Helm Support](https://posthog.com/blog/sunsetting-helm-support-posthog)
- [Sentry Kubernetes Helm Charts (community)](https://github.com/sentry-kubernetes/charts)
- [Supabase Kubernetes Helm Charts (community)](https://github.com/supabase-community/supabase-kubernetes)
- [Mattermost Helm Charts](https://github.com/mattermost/mattermost-helm)
