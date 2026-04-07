# Docker Compose Deployment

## Why

Full stack on your machine. All services, database, Redis — everything running locally. Best for development, testing, and self-hosted production without Kubernetes.

Before self-hosting, consider the hosted service at [vexa.ai](https://vexa.ai) — get an API key, no deployment needed. For simpler self-hosting, see [Vexa Lite](../lite/README.md).

## What

Runs all Vexa services via Docker Compose:

- API Gateway (port 8056)
- Admin API, Meeting API, Runtime API, MCP
- Dashboard
- TTS Service
- PostgreSQL + Redis + MinIO
- Bots spawn as Docker containers (needs Docker socket)
- **Experimental (commented out):** Agent API, Calendar Service — uncomment in `docker-compose.yml` to enable

**You provide:** A transcription service — use [Vexa transcription](https://vexa.ai) (ready to go) or [self-host](../../services/transcription-service/README.md) with GPU.

## Prerequisites

Fresh Linux machine (tested on Ubuntu 24.04):

```bash
apt-get update && apt-get install -y make
curl -fsSL https://get.docker.com | sh
```

## How

### Quick start

```bash
git clone https://github.com/Vexa-ai/vexa.git
cd vexa/deploy/compose
make all
```

That's it. Pulls pre-built images from DockerHub, starts services, syncs DB schema, creates API key, tests connectivity. To build from source instead: `make all-build`.

**Before running**, edit `.env`:

1. Set `TRANSCRIPTION_SERVICE_URL` — get a key at [vexa.ai](https://vexa.ai) or [self-host](../../services/transcription-service/README.md)

### Make targets


| Target                 | What it does                                         |
| ---------------------- | ---------------------------------------------------- |
| `make all`             | Full setup: env → pull → up → init-db → api-key → test  |
| `make all-build`       | Same but builds images from source                       |
| `make env`             | Create .env from template, or patch missing vars     |
| `make build`           | Build all images with immutable timestamp tag        |
| `make up`              | Start services using last-built tag                  |
| `make down`            | Stop all services                                    |
| `make init-db`         | Idempotent schema sync (creates tables if missing)   |
| `make setup-api-key`   | Create default user + VEXA_API_KEY for dashboard     |
| `make ps`              | Show running containers                              |
| `make logs`            | Tail all service logs                                |
| `make test`            | Health check all services + show URLs + current tag  |
| `make test-transcription` | Send test audio to transcription service, verify text comes back |
| `make restore-db`      | Restore a `pg_dump` into local postgres              |
| `make publish`         | Push all images to DockerHub + update `:dev` pointer |
| `make promote-staging` | Set `:staging` to TAG= (or last built)               |
| `make promote-latest`  | Set `:latest` to TAG= (or last built)                |
| `make help-tags`       | Show tagging workflow help                           |


### Image tagging

Every `make build` produces immutable version+timestamp-tagged images (`VERSION-YYMMDD-HHMM`):

```bash
make build              # → vexaai/api-gateway:0.10.0-260330-1415, etc.
make up                 # runs those exact images (tag read from .last-tag)
```

You always know what you're running. The tag is saved to `deploy/compose/.last-tag` (gitignored). Override with `IMAGE_TAG=custom make build`.

**Publishing to DockerHub** — only needed if you want to push custom images to your own registry (e.g. for deploying to remote servers or Kubernetes). Not required for local development.

```bash
make publish                         # pushes + updates :dev on DockerHub
make promote-staging TAG=260330-1415 # re-points :staging
make promote-latest TAG=260330-1415  # re-points :latest
```

### Configuration

Edit `.env` at repo root. Created from [deploy/env/env-example](../env/env-example).

**Required:**


| Variable                  | Description                                                                                                           |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| TRANSCRIPTION_SERVICE_URL | Your transcription endpoint. Get at [vexa.ai](https://vexa.ai) or [self-host](../../services/transcription-service/README.md). |


Everything else has working defaults for local dev.

**Optional:**


| Variable              | Default                      | Description                                      |
| --------------------- | ---------------------------- | ------------------------------------------------ |
| IMAGE_TAG             | dev                          | Docker image tag. `dev` pulls from DockerHub. `make build` overrides with local tag. |
| DASHBOARD_HOST_PORT   | 3001                         | Dashboard port                                   |
| REMOTE_DB             | false                        | Use external Postgres instead of local           |
| LOCAL_TRANSCRIPTION   | false                        | Run transcription-service locally (needs GPU)    |
| BOT_IMAGE_NAME        | vexaai/vexa-bot:dev          | Bot Docker image (follows IMAGE_TAG when built)  |
| API_GATEWAY_HOST_PORT | 8056                         | API Gateway port                                 |
| ADMIN_API_PORT        | 8057                         | Admin API port                                   |
| ADMIN_TOKEN           | changeme                     | Admin API authentication token                   |


Full env reference: [deploy/env/](../env/README.md)

### External database

```bash
# In .env:
REMOTE_DB=true
DB_HOST=your-postgres-host
DB_PORT=5432
DB_NAME=vexa
DB_USER=postgres
DB_PASSWORD=your-password
```

### Local GPU transcription

```bash
# In .env:
LOCAL_TRANSCRIPTION=true
# Then make up will also start services/transcription-service/
```

### Files


| File               | Purpose                          |
| ------------------ | -------------------------------- |
| docker-compose.yml | Main stack definition            |
| Makefile           | All targets for compose workflow |


## Development Notes

### Service ports (internal)


| Service                 | Port  | Health/Verify                 |
| ----------------------- | ----- | ----------------------------- |
| API Gateway             | 8056  | `curl http://localhost:8056/` |
| Admin API               | 8057  | Swagger at `/docs`            |
| Meeting API             | 8080  | `/health`                     |
| Runtime API             | 8090  | `/health`                     |
| Agent API *(experimental)* | 8100  | `/health`                     |
| MCP                     | 18888 | MCP protocol                  |
| TTS Service             | 8002  | (internal only)               |
| Calendar Service *(experimental)* | 8050  | `/health`                     |
| Dashboard               | 3001  | HTML page loads               |
| PostgreSQL              | 5458  | `pg_isready` (host port)      |
| MinIO                   | 9000  | Bucket `vexa-recordings`      |


### Startup dependency order

Services should start in this order due to dependencies:

1. **Infra:** PostgreSQL, Redis, MinIO
2. **Foundation:** Admin API, Runtime API
3. **Dependent:** Meeting API, API Gateway, MCP, TTS Service *(+ Agent API, Calendar Service if enabled)*
4. **Frontend:** Dashboard

### Upgrading from pre-0.10

`make all` handles schema migration automatically. `init-db` runs idempotent schema sync on both admin and meeting models — it adds missing columns and indexes without dropping data.

New columns added in 0.10:

| Table          | Column         | Default   |
| -------------- | -------------- | --------- |
| api_tokens     | scopes         | `'{}'`    |
| api_tokens     | name           | `''`      |
| api_tokens     | last_used_at   | NULL      |
| api_tokens     | expires_at     | NULL      |
| transcriptions | segment_id     | NULL      |

If you have an existing database (local or external), just run `make all` — the schema sync will converge it. No manual migration needed.

### Cleanup

Always stop the stack before restarting, even on failure:

```bash
make down && docker compose ps  # should be empty
```

### Security

- Never log secrets (`ADMIN_API_TOKEN`, DB credentials, API keys). Log that they are set, not their values.
- Create test users/meetings per run. Do not reuse data from previous runs.

## Definition of Done

Validated by [tests/00-docs-compose.md](../../tests/00-docs-compose.md).

| #  | Item | Weight | Test | Status | Last checked |
|----|------|--------|------|--------|--------------|
| 1  | `make all` from clean clone | 10 | S1 | PASS | 2026-04-06 |
| 2  | `make build` produces VERSION-YYMMDD-HHMM tags | 8 | S12 | PASS | 2026-04-06 |
| 3  | `make up` starts all healthy | 8 | S2 | PASS | 2026-04-06 |
| 4  | Port table matches compose + env-example | 8 | S3 | PASS | 2026-04-06 |
| 5  | Configuration defaults three-way agree | 8 | S5 | PASS | 2026-04-06 |
| 6  | Make targets exist and match descriptions | 5 | S4 | PASS | 2026-04-06 |
| 7  | Transcription reachable from containers | 5 | S3 | SKIP | — |
| 8  | Dashboard accessible | 4 | S3 | PASS | 2026-04-06 |
| 9  | REMOTE_DB path works | 7 | S8 | PASS | 2026-04-06 |
| 10 | LOCAL_TRANSCRIPTION path works | 5 | S9 | PASS | 2026-04-06 |
| 11 | Schema migration (pre-0.10 upgrade) | 7 | S10 | PASS | 2026-04-06 |
| 12 | Pre-built images (skip build) | 5 | S11 | PASS | 2026-04-08 |
| 13 | restore-db works | 4 | S13 | PASS | 2026-04-06 |
| 14 | Dependency order matches compose | 4 | S6 | PASS | 2026-04-06 |
| 15 | Cleanup leaves no containers | 2 | S7 | PASS | 2026-04-06 |
| 16 | Files table entries exist | 2 | S14 | PASS | 2026-04-06 |
| 17 | All internal links resolve | 3 | S15 | PASS | 2026-04-06 |
| 18 | No secrets logged | 3 | S16 | PASS | 2026-04-06 |
| 19 | DoD cross-refs complete | 2 | S17 | PASS | 2026-04-06 |

## Confidence

Score: 93/100
Last validated: 2026-04-08
Ceiling: Transcription not tested from inside containers

