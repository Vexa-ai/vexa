# Docker Compose Deployment

## Why

Full stack on your machine. All services, database, Redis — everything running locally. Best for development, testing, and self-hosted production without Kubernetes.

Before self-hosting, consider the hosted service at [vexa.ai](https://vexa.ai) — get an API key, no deployment needed. For simpler self-hosting, see [Vexa Lite](../lite/README.md).

## What

Runs all Vexa services via Docker Compose:

- API Gateway (port 8056)
- Admin API, Meeting API, Runtime API, Agent API, MCP
- Dashboard
- TTS Service
- PostgreSQL + Redis + MinIO
- Bots spawn as Docker containers (needs Docker socket)

**You provide:** A transcription service — use [Vexa transcription](https://vexa.ai) (ready to go) or [self-host](../../services/transcription-service/) with GPU.

## How

### Quick start

```bash
# From repo root:
make all
```

That's it. Copies env-example → .env, builds images, starts services, runs migrations, tests connectivity.

**Before running**, edit `.env`:

1. Set `DASHBOARD_PATH` to your [vexa-dashboard](https://github.com/Vexa-ai/vexa-dashboard) checkout (absolute path)
2. Set `TRANSCRIPTION_SERVICE_URL` — get a key at [vexa.ai](https://vexa.ai) or [self-host](../../services/transcription-service/)

### Make targets


| Target                        | What it does                                         |
| ----------------------------- | ---------------------------------------------------- |
| `make all`                    | Full setup: env → build → up → migrate → test        |
| `make env`                    | Create .env from template (if not exists)            |
| `make build`                  | Build all images with immutable timestamp tag        |
| `make up`                     | Start services using last-built tag                  |
| `make down`                   | Stop all services                                    |
| `make ps`                     | Show running containers                              |
| `make logs`                   | Tail all service logs                                |
| `make test`                   | Health check all services + show URLs + current tag  |
| `make publish`                | Push all images to DockerHub + update `:dev` pointer |
| `make promote-staging`        | Set `:staging` to TAG= (or last built)               |
| `make promote-latest`         | Set `:latest` to TAG= (or last built)                |
| `make help-tags`              | Show tagging workflow help                           |
| `make migrate`                | Run database migrations                              |
| `make migrate-or-init`        | Smart: init fresh DB or migrate existing             |
| `make makemigrations M="msg"` | Create new migration                                 |
| `make migration-status`       | Show current migration version                       |


### Image tagging

Every `make build` produces immutable timestamp-tagged images (`YYMMDD-HHMM`):

```bash
make build              # → vexaai/api-gateway:260330-1415, vexaai/admin-api:260330-1415, etc.
make up                 # runs those exact images (tag read from .last-tag)
```

You always know what you're running. Mutable tags (`:dev`, `:staging`, `:latest`) are only updated during publication:

```bash
make publish                         # pushes + updates :dev on DockerHub
make promote-staging TAG=260330-1415 # re-points :staging
make promote-latest TAG=260330-1415  # re-points :latest
```

The tag is saved to `deploy/compose/.last-tag` (gitignored). Override with `IMAGE_TAG=custom make build`.

### Configuration

Edit `.env` at repo root. Created from [deploy/env/env-example](../env/env-example).

**Required:**


| Variable                  | Description                                                                                                           |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| DASHBOARD_PATH            | Absolute path to [vexa-dashboard](https://github.com/Vexa-ai/vexa-dashboard) checkout                                 |
| TRANSCRIPTION_SERVICE_URL | Your transcription endpoint. Get at [vexa.ai](https://vexa.ai) or [self-host](../../services/transcription-service/). |


Everything else has working defaults for local dev.

**Optional:**


| Variable              | Default                      | Description                                      |
| --------------------- | ---------------------------- | ------------------------------------------------ |
| DASHBOARD_HOST_PORT   | 3001                         | Dashboard port                                   |
| REMOTE_DB             | false                        | Use external Postgres instead of local           |
| LOCAL_TRANSCRIPTION   | false                        | Run transcription-service locally (needs GPU)    |
| BOT_IMAGE_NAME        | vexaai/vexa-bot:${IMAGE_TAG} | Bot Docker image (uses same tag as all services) |
| API_GATEWAY_HOST_PORT | 8056                         | API Gateway port                                 |
| ADMIN_API_HOST_PORT   | 8057                         | Admin API port                                   |


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


| File                        | Purpose                                       |
| --------------------------- | --------------------------------------------- |
| docker-compose.yml          | Main stack definition                         |
| docker-compose.local-db.yml | PostgreSQL overlay (used when REMOTE_DB≠true) |
| Makefile                    | All targets for compose workflow              |


## Development Notes

### Service ports (internal)


| Service                 | Port  | Health/Verify                 |
| ----------------------- | ----- | ----------------------------- |
| API Gateway             | 8056  | `curl http://localhost:8056/` |
| Admin API               | 8057  | Swagger at `/docs`            |
| Meeting API             | 8080  | `/health`                     |
| Runtime API             | 8090  | `/health`                     |
| Agent API               | 8100  | `/health`                     |
| MCP                     | 18888 | MCP protocol                  |
| TTS Service             | 8002  | (internal only)               |
| Calendar Service        | 8050  | `/health`                     |
| Dashboard               | 3001  | HTML page loads               |
| PostgreSQL              | 5458  | `pg_isready`                  |
| MinIO                   | 9000  | Bucket `vexa-recordings`      |


### Startup dependency order

Services should start in this order due to dependencies:

1. **Infra:** PostgreSQL, Redis, MinIO
2. **Foundation:** Admin API, Runtime API
3. **Dependent:** Meeting API, Agent API, API Gateway, MCP, TTS Service, Calendar Service
4. **Frontend:** Dashboard

### Cleanup

Always stop the stack before restarting, even on failure:

```bash
make down && docker compose ps  # should be empty
```

### Security

- Never log secrets (`ADMIN_API_TOKEN`, DB credentials, API keys). Log that they are set, not their values.
- Create test users/meetings per run. Do not reuse data from previous runs.

## Definition of Done

| # | Item | Weight | Status | Evidence | Last checked |
|---|------|--------|--------|----------|--------------|
| 1 | `make all` from clean clone | 20 | PASS | Validated in previous sessions | 2026-04-04 |
| 2 | `make build` produces YYMMDD-HHMM tags | 15 | PASS | Tags confirmed: 260405-2029 etc. | 2026-04-05 |
| 3 | `make up` starts all healthy | 15 | PASS | All services reach healthy state | 2026-04-04 |
| 4 | Port table matches docker-compose.yml | 10 | SKIP | Not audited this run | — |
| 5 | Inter-container DNS works | 10 | PASS | Services communicate via Docker network | 2026-04-04 |
| 6 | `make test` passes | 10 | PASS | Health checks pass | 2026-04-04 |
| 7 | Transcription reachable from containers | 10 | SKIP | Not tested from inside compose containers this run | — |
| 8 | Dashboard accessible | 10 | PASS | Dashboard on 3001, all backend calls OK | 2026-04-05 |

## Confidence

Score: 75/100
Last validated: 2026-04-05 (full-stack-lite run)
Ceiling: Port table unaudited, transcription not tested from inside compose containers

