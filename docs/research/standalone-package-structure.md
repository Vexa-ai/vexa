# Standalone Package Structure for Monorepo-Extracted Projects

> Research: How open-source infra projects structure standalone repos for self-contained clone-build-test-deploy experience.

---

## Table of Contents

1. [How Major Projects Structure Their Standalone Repos](#1-how-major-projects-structure-their-standalone-repos)
2. [Monorepo to Standalone Extraction Patterns](#2-monorepo-to-standalone-extraction-patterns)
3. [Minimal Viable Standalone Package Structure](#3-minimal-viable-standalone-package-structure)
4. [Testing in Standalone Context](#4-testing-in-standalone-context)
5. [Helm Charts: In-Repo vs Separate](#5-helm-charts-in-repo-vs-separate)
6. [Recommended Structure for runtime-api](#6-recommended-structure-for-runtime-api)

---

## 1. How Major Projects Structure Their Standalone Repos

### Temporal (Go, task orchestration, 12K stars)

**Root files (12 only):** `.dockerignore`, `.gitattributes`, `.gitignore`, `.gitmodules`, `.goreleaser.yml`, `AGENTS.md`, `CONTRIBUTING.md`, `LICENSE` (MIT), `Makefile` (726 lines, 80+ targets), `README.md`, `go.mod`, `go.sum`

**Three-tier deployment separation:**
- **Tier 1 — In-repo dev deps** (`develop/docker-compose/`): Docker-compose for databases only. Server runs from source via `make start`. Zero-Docker option: `make start` uses SQLite.
- **Tier 2 — Full docker-compose for users** (separate repo, now archived → migrated to `temporalio/samples-server/compose`): Full stack in Docker for try-it-out.
- **Tier 3 — Helm chart** (separate repo `temporalio/helm-charts`): Production K8s. User provides persistence.

**Makefile categories:** Build (5 binaries), proto compilation, code quality (lint/fmt), three-tier testing (unit/integration/functional with `-coverage` variants), schema installation, server startup (12 variants by DB backend), tool management (19 pinned dev tools).

**DX files:** `CONTRIBUTING.md` (CLA, prereqs, 3 test tiers, IDE config), issue templates (bug + feature), PR template (what/why/how-tested checklist), `CODEOWNERS`, AI agent configs (`AGENTS.md`, `.claude/`, `.cursor/`).

### Traefik (Go, reverse proxy, 53K stars)

**Root files:** `.goreleaser.yml.tmpl`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `Dockerfile` (12 lines, packaging-only), `LICENSE.md`, `Makefile` (26 targets), `README.md`, `SECURITY.md`, `flake.nix`, `traefik.sample.toml`, `traefik.sample.yml`

**No docker-compose anywhere.** Single binary — just run it.

**Helm chart:** Separate repo (`traefik/traefik-helm-chart`). Multiple charts with different release cadences.

**Layout:** `cmd/` (entrypoints), `pkg/` (public), `internal/` (private), `integration/` (40+ test files), `docs/`, `webui/` (dashboard), `contrib/` (grafana dashboards, systemd units), `script/` (CI/build).

### MinIO (Go, object storage, 49K stars)

**Root files:** 6 Dockerfiles (different scenarios), `Makefile` (50+ targets), `CONTRIBUTING.md`, `COMPLIANCE.md`, `SECURITY.md`, `VULNERABILITY_REPORT.md`, `main.go` directly at root.

**Helm chart in-repo:** `helm/minio/` with self-hosted index.yaml. Community-maintained; MinIO officially recommends their Kubernetes Operator for production.

**Makefile:** Extremely granular test targets (30+ scenario-specific: `test-ilm`, `test-decom`, `test-versioning`, `test-replication-2site`, etc.).

### Grafana Loki (Go, log aggregation, 24K stars)

**No docker-compose at root.** Two compose files nested:
- `production/docker-compose.yaml` — minimal 3-service stack
- `production/docker/docker-compose.yaml` — full scalable stack (3 Loki replicas, nginx, MinIO, Prometheus, Grafana)

**Helm charts in-repo:** `production/helm/` with 4 charts (loki, loki-stack, fluent-bit, meta-monitoring). 5 dedicated Helm CI workflows.

**`production/` directory** as deployment umbrella: docker/, helm/, ksonnet/, nomad/, terraform/. Clear separation of deploy from source.

**Makefile:** 90+ targets. K3d local K8s dev (`make dev-k3d-loki`).

### Cross-Project Comparison

| Aspect | Temporal | Traefik | MinIO | Loki |
|--------|----------|---------|-------|------|
| Root docker-compose | No | No | No | No (nested in `production/`) |
| Dockerfile at root | No | 1 (packaging) | 6 variants | No |
| Makefile targets | 80+ | ~26 | 50+ | 90+ |
| Helm chart location | Separate repo | Separate repo | In-repo `helm/` | In-repo `production/helm/` |
| CI workflows | 18 | 13 | 17+ | 41 |
| Sample configs at root | No | Yes (.toml, .yml) | No | No |
| Build inside Docker | No | No | No | No |
| Local K8s dev | No | No | No | Yes (k3d) |

**Key patterns:**
1. **None put docker-compose at root.** Either absent or nested under `production/` / `deploy/`.
2. **Dockerfiles at root are packaging-only** — copy pre-built binary, never compile inside.
3. **Makefile is the universal build interface** even with language-native tooling.
4. **`production/` or `deploy/` directory** separates deployment from source (Loki is most explicit).
5. **Helm placement scales with project size**: small/focused → separate repo; large multi-component → in-repo.

---

## 2. Monorepo to Standalone Extraction Patterns

### Symfony (50+ components extracted via splitsh-lite)

**Critical finding: ZERO standalone-only files.** Every file the standalone repo needs is maintained inside the monorepo component directory. The split is purely extractive.

Files in each monorepo component dir (= standalone repo root):
```
.gitattributes          # export-ignore rules for dist installs
.gitignore              # vendor/, lock files
.github/
  PULL_REQUEST_TEMPLATE.md   # "Submit PRs to main repo, not here"
  workflows/
    close-pull-request.yml   # Auto-closes PRs on read-only mirror
CHANGELOG.md
LICENSE                 # MIT, no .md extension
README.md               # Brief (3 paragraphs), links back to main repo
composer.json           # Full standalone metadata, version-range deps (not path refs)
phpunit.xml.dist        # Full test config, bootstraps from vendor/autoload.php
Tests/                  # Full test suite included
```

**`.gitattributes` controls dist packaging:**
```
/Tests export-ignore
/phpunit.xml.dist export-ignore
/.git* export-ignore
```

**PR redirect workflow** — belt-and-suspenders: both a PR template saying "don't PR here" AND an auto-close workflow.

### Laravel (33 components extracted via splitsh-lite)

**Also zero standalone-only files.** Differences from Symfony:

| Aspect | Symfony | Laravel |
|--------|---------|---------|
| Tests in component dir | Yes | No (tests elsewhere) |
| `CHANGELOG.md` | Yes | No |
| `phpunit.xml.dist` | Yes | No |
| README depth | Brief (3 paragraphs) | Detailed (standalone usage examples) |

**Split infrastructure:** `bin/split.sh` calls splitsh-lite per prefix, force-pushes. Uses a **dedicated splitter server** (not ephemeral CI) because splitsh-lite's SQLite cache makes rescans too expensive on ephemeral runners.

### What This Means for Us

**All files the standalone repo needs must exist inside `services/runtime-api/` in the monorepo.** This includes:

**Must have (extracted with the split):**
- `LICENSE`
- `README.md` (standalone-focused, links back to main repo for contributing)
- `pyproject.toml` (with version-range deps, not path refs)
- `.gitignore`
- `.gitattributes` (export-ignore for tests, dev files)
- `.github/workflows/close-pull-request.yml`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `docker-compose.yml`
- `Makefile`
- `Dockerfile`
- `tests/`

**Monorepo-only files (NOT in component dir):**
- Root `pyproject.toml` with workspace config
- `bin/split.sh` — split and release scripts
- `.github/workflows/split.yml` — orchestration workflow
- Root CI workflows (test matrix spanning all packages)
- Root `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`

---

## 3. Minimal Viable Standalone Package Structure

### What's Universal (13/13 surveyed projects)

Based on: Hatchet, Infisical, Windmill, Coolify, Dokploy, Pocketbase, Valkey, E2B, FastAPI, Celery, Prefect, Dagster, Airflow.

| File | Prevalence | Notes |
|------|-----------|-------|
| `README.md` | 13/13 | |
| `LICENSE` | 13/13 | MIT gets explicit HN praise |
| `.gitignore` | 13/13 | |
| `.github/` with CI workflows | 12/13 | Tests must visibly run |
| `CONTRIBUTING.md` | 10/13 | |
| `.dockerignore` | 9/13 | |
| `Makefile` or equivalent | 8/13 | |
| `SECURITY.md` | 7/13 | **Unfilled template is worse than absent** |
| `CHANGELOG.md` | 6/13 | |
| `.pre-commit-config.yaml` | 5/5 Python | Universal in Python infra |

### Self-Hosted Infra Projects Specifically

Every self-hosted project (Hatchet, Infisical, Coolify, Windmill) has:
- `docker-compose.yml` at root or `deploy/` — local dev and simple self-hosting
- `.env.example` files
- `Dockerfile` at root

### What HN/Reddit Reviewers Check (In Order)

1. **License file** (checked first; MIT gets explicit positive callouts)
2. **README quality** (logo, badges, one-liner, quickstart)
3. **Whether `docker compose up` actually works**
4. **CI/CD presence** (GitHub Actions — do tests run?)
5. **Commit history and issue resolution speed**
6. **Code organization**

### Red Flags That Destroy Credibility

- `node_modules` or build artifacts committed
- Template/placeholder content (unfilled SECURITY.md)
- SaaS dependencies in "open-source" (mandatory proprietary services)
- Database lock-in without alternatives
- Superlatives in README ("fastest", "best", "revolutionary")
- Corporate/marketing voice (HN wants engineer-to-engineer)

### HN Launch Stats

Projects gain ~121 stars within 24 hours, ~189 within 48 hours, ~289 within a week of HN front-page exposure (based on 138 launches, 2024-2025).

### Python Infra Makefile Standard Targets

```makefile
# Essential (every project)
make test              # run test suite
make lint              # run linters
make help              # show targets

# Very Common
make install           # install dev dependencies
make format            # auto-format code
make clean             # remove artifacts
make build             # build package or docker image

# Self-Hosted Projects
make up                # docker compose up
make down              # docker compose down
make up-dev            # docker compose dev mode
make logs              # docker compose logs

# Release/CI
make release           # build + publish
make cov               # test with coverage
make check             # all quality checks combined
```

---

## 4. Testing in Standalone Context

### Four Patterns (Ordered by Modernity)

**Pattern 1: Testcontainers (modern, preferred for new Python projects)**
- Session-scoped pytest fixtures spin up Postgres/Redis containers programmatically
- No docker-compose file needed for tests
- Package: `testcontainers[postgres,redis]` on PyPI
- Used by newer projects; growing rapidly

**Pattern 2: docker-compose.test.yml (established)**
```
# Examples:
Infisical:  docker-compose.bdd.yml, docker-compose.e2e-dbs.yml
Hatchet:    docker-compose.infra.yml (infrastructure deps only)
Coolify:    docker-compose.dev.yml + .env.testing
Celery:     docker/ directory with compose for RabbitMQ, Redis, various backends
```
`pytest-docker` plugin integrates compose lifecycle with pytest fixtures.

**Pattern 3: Root conftest.py for CI orchestration (Dagster pattern)**
- Root `conftest.py` has NO service fixtures — only test organization:
  - Warning suppression
  - `--split` CLI option for parallel CI
  - CI detection via env vars to skip integration tests
  - Custom markers
- Each package has its own conftest.py with actual fixtures

**Pattern 4: GitHub Actions `services:` block**
```yaml
jobs:
  test:
    services:
      redis:
        image: redis:7
        ports: ["6379:6379"]
    steps:
      - run: pytest --cov
```
Most common pattern for CI specifically. No compose file needed.

### Recommended conftest.py Architecture

```
services/runtime-api/
  conftest.py              # Root: markers, CI detection, warning filters
  tests/
    conftest.py            # Unit: mocks, test data, fixtures
    test_api.py
    test_profiles.py
  integration_tests/       # Separate directory (Prefect/Dagster pattern)
    conftest.py            # Testcontainers or compose fixtures
    test_redis.py
    test_docker_backend.py
  docker-compose.test.yml  # For local integration testing
```

### Key Principle

Standalone packages must test without the rest of the monorepo. This means:
- **No imports from sibling packages** in tests
- **Test fixtures are self-contained** (vendored or generated)
- **CI workflow runs independently** with its own `services:` block
- **conftest.py bootstraps from the package itself**, not a shared root

---

## 5. Helm Charts: In-Repo vs Separate

### Current Landscape

| Pattern | Projects | Why |
|---------|----------|-----|
| **In-repo** | cert-manager (`deploy/charts/`), MinIO (`helm/`), Loki (`production/helm/`), Linkerd (`charts/`) | Single team owns both app and chart; atomic PRs |
| **Separate repo** | Traefik, ArgoCD, Grafana, Prometheus, Temporal, Cilium | Different teams maintain chart vs app; multi-project chart repos; community-maintained |

### Decision Framework

**Separate repo when:** Different teams maintain chart vs app, one chart repo serves multiple projects, or chart has independent release cadence.

**In-repo when (our case):** Single team, single product, chart changes are tightly coupled to code changes.

### Modern Consensus (2024-2026)

**In-repo + OCI registry is winning for single-product projects.** The "separate chart repo" pattern is legacy from the deprecated `helm/charts` monorepo era (2020).

**OCI registries are mainstream:**
- Bitnami defaulted to OCI (November 2024) — largest chart provider
- Traefik publishes to `oci://ghcr.io/traefik/helm/traefik`
- ArgoCD publishes to `oci://ghcr.io/argoproj/argo-helm/argo-cd`
- No `index.yaml` needed; unified auth with container images

### Minimum K8s Deploy Story (Priority Order)

1. `docker-compose.yml` — local dev and simple self-hosting (day 1)
2. Helm chart at `deploy/helm/` or `charts/` — for K8s users (week 1)
3. Published to GHCR OCI via GitHub Actions — `helm install` from anywhere (week 2)
4. Good defaults in `values.yaml` — works out of the box

### OCI Publishing Workflow (~15 lines)

```yaml
name: Publish Helm Chart
on:
  push:
    branches: [main]
    paths: ['deploy/helm/charts/**']
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - name: Login to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ github.token }}
      - name: Package and push
        run: |
          VERSION=$(yq -r .version charts/runtime-api/Chart.yaml)
          helm package charts/runtime-api --version $VERSION
          helm push runtime-api-$VERSION.tgz oci://ghcr.io/${{ github.repository_owner }}/charts
```

Users install with: `helm install runtime-api oci://ghcr.io/vexa-ai/charts/runtime-api`

---

## 6. Recommended Structure for runtime-api

Based on all research above, here is the target file layout for `services/runtime-api/` (= what the standalone repo will look like after splitsh-lite extraction):

```
services/runtime-api/
├── .dockerignore
├── .gitattributes                    # export-ignore for tests, dev files
├── .gitignore
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md
│   │   └── feature_request.md
│   ├── PULL_REQUEST_TEMPLATE.md      # "Submit PRs to main repo"
│   └── workflows/
│       ├── close-pull-request.yml    # Auto-close PRs on read-only mirror
│       ├── ci.yml                    # Lint + unit tests
│       └── publish.yml               # Docker image + Helm chart to OCI
├── .pre-commit-config.yaml
├── CHANGELOG.md
├── CONTRIBUTING.md                   # Brief, links to main repo
├── Dockerfile
├── LICENSE                           # Apache-2.0
├── Makefile
├── README.md                         # Standalone-focused with quickstart
├── docker-compose.yml                # runtime-api + Redis
├── docker-compose.test.yml           # For integration tests
├── profiles.example.yaml
├── pyproject.toml                    # Version-range deps, not path refs
│
├── charts/                           # Helm chart
│   └── runtime-api/
│       ├── Chart.yaml
│       ├── values.yaml
│       ├── templates/
│       │   ├── deployment.yaml
│       │   ├── service.yaml
│       │   ├── configmap.yaml
│       │   └── _helpers.tpl
│       └── README.md
│
├── runtime_api/                      # Source code (existing)
│   ├── __init__.py
│   ├── main.py
│   ├── api.py
│   ├── config.py
│   ├── profiles.py
│   ├── lifecycle.py
│   ├── state.py
│   └── backends/
│       ├── __init__.py
│       ├── docker.py
│       ├── kubernetes.py
│       └── process.py
│
├── tests/                            # Unit tests (existing)
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_profiles.py
│   ├── test_backends.py
│   ├── test_lifecycle.py
│   └── test_state.py
│
└── integration_tests/                # Integration tests (new)
    ├── conftest.py                   # Testcontainers or compose fixtures
    ├── test_redis.py
    └── test_docker_backend.py
```

### Makefile Targets

```makefile
.DEFAULT_GOAL := help

help:            ## Show this help
install:         ## Install dev dependencies (uv sync)
test:            ## Run unit tests
test-integration:## Run integration tests (requires Docker)
lint:            ## Run ruff check + mypy
format:          ## Run ruff format
build:           ## Build Docker image
up:              ## docker compose up -d
down:            ## docker compose down
logs:            ## docker compose logs -f
clean:           ## Remove build artifacts
helm-lint:       ## Lint Helm chart
helm-template:   ## Render Helm templates locally
publish:         ## Build + push Docker image
```

### docker-compose.yml

```yaml
services:
  runtime-api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - REDIS_URL=redis://redis:6379
    volumes:
      - ./profiles.example.yaml:/app/profiles.yaml:ro
      - /var/run/docker.sock:/var/run/docker.sock  # for Docker backend
    depends_on:
      - redis
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

### What's New vs Current

| File | Status | Notes |
|------|--------|-------|
| `docker-compose.yml` | **NEW** | runtime-api + Redis |
| `docker-compose.test.yml` | **NEW** | Integration test infra |
| `Makefile` | **NEW** | Standard targets |
| `charts/runtime-api/` | **NEW** | Helm chart |
| `.github/workflows/ci.yml` | **NEW** | Lint + test |
| `.github/workflows/publish.yml` | **NEW** | Docker + Helm OCI push |
| `.github/workflows/close-pull-request.yml` | **NEW** | Read-only mirror guard |
| `.github/PULL_REQUEST_TEMPLATE.md` | **NEW** | Redirect to main repo |
| `.github/ISSUE_TEMPLATE/` | **NEW** | Bug + feature |
| `CONTRIBUTING.md` | **NEW** | Brief, links to main repo |
| `CHANGELOG.md` | **NEW** | Version history |
| `LICENSE` | **NEW** (in this dir) | Apache-2.0 |
| `.gitattributes` | **NEW** | export-ignore rules |
| `.pre-commit-config.yaml` | **NEW** | ruff, mypy |
| `integration_tests/` | **NEW** | Separate from unit tests |
| `Dockerfile` | EXISTS | May need review |
| `pyproject.toml` | EXISTS | Ensure version-range deps |
| `README.md` | EXISTS | Rewrite for standalone |
| `profiles.example.yaml` | EXISTS | |
| `runtime_api/` | EXISTS | |
| `tests/` | EXISTS | |

---

## Sources

### Repos Examined
- [temporalio/temporal](https://github.com/temporalio/temporal) — Makefile, CI, 3-tier deploy
- [traefik/traefik](https://github.com/traefik/traefik) — Minimal root, separate Helm
- [minio/minio](https://github.com/minio/minio) — In-repo Helm, granular test targets
- [grafana/loki](https://github.com/grafana/loki) — `production/` deployment umbrella
- [symfony/symfony](https://github.com/symfony/symfony) → [symfony/http-kernel](https://github.com/symfony/http-kernel) — splitsh-lite extraction
- [laravel/framework](https://github.com/laravel/framework) → [illuminate/database](https://github.com/illuminate/database) — splitsh-lite extraction
- [hatchet-dev/hatchet](https://github.com/hatchet-dev/hatchet), [Infisical/infisical](https://github.com/Infisical/infisical), [windmill-labs/windmill](https://github.com/windmill-labs/windmill), [coollabsio/coolify](https://github.com/coollabsio/coolify), [Dokploy/dokploy](https://github.com/Dokploy/dokploy) — Self-hosted infra patterns
- [tiangolo/fastapi](https://github.com/tiangolo/fastapi), [celery/celery](https://github.com/celery/celery), [PrefectHQ/prefect](https://github.com/PrefectHQ/prefect), [dagster-io/dagster](https://github.com/dagster-io/dagster), [apache/airflow](https://github.com/apache/airflow) — Python infra patterns
- [cert-manager/cert-manager](https://github.com/cert-manager/cert-manager), [linkerd/linkerd2](https://github.com/linkerd/linkerd2) — In-repo Helm patterns
- [supabase/supabase](https://github.com/supabase/supabase) — Docker-compose-first self-hosting

### Articles & Analysis
- [Hatchet: 2 years of OSS](https://hatchet.run/blog/two-years-open-source) — MIT licensing, HN launch lessons
- [HN launch guide for dev tools](https://www.markepear.dev/blog/dev-tool-hacker-news-launch) — Reviewer behavior
- [HN star-driving research](https://arxiv.org/html/2511.04453v1) — Star acquisition rates
- [Bitnami OCI migration](https://blog.bitnami.com/2024/10/bitnami-helm-charts-moving-to-oci.html) — OCI mainstream signal
- [Helm OCI docs](https://helm.sh/docs/topics/registries/) — Official guidance
- [Helm vs Kustomize 2025](https://justinpolidori.com/posts/20250815_helm_kustomize/) — Decision framework
