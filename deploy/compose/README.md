# deploy/compose — the v0.12 control-plane stack (P4)

`docker-compose.yml` brings up the v0.12 control plane: the infra (`postgres:17-alpine`,
`redis:7-alpine`, `minio` + `minio-init`) and the four long-running Python services, each
building its own slim `uv`-based image from `<service>/Dockerfile`:

| service      | build context                          | host port | entrypoint                         |
|--------------|----------------------------------------|-----------|------------------------------------|
| admin-api    | `identity/services/admin-api`          | 18057     | `python -m admin_api`              |
| runtime      | `runtime`                              | 18090     | `python -m runtime_kernel`         |
| meeting-api  | `meetings/services/meeting-api`        | 18080     | `python -m meeting_api`            |
| gateway      | `gateway/services/gateway`             | 18056     | `python -m gateway`                |

Every service answers `GET /health` and carries a compose healthcheck; `depends_on` waits on
`condition: service_healthy` so the bring-up is ordered. The `runtime` mounts
`/var/run/docker.sock` and spawns the bot (`BROWSER_IMAGE=vexaai/vexa-bot:dev`, published — a
reference, never built here) on demand; the bot is NOT a compose service.

## Usage

```bash
cp .env.example .env            # edit secrets/ports/DOCKER_GID
docker compose -f deploy/compose/docker-compose.yml build
docker compose -f deploy/compose/docker-compose.yml up -d
# poll until healthy, then:
curl -sf http://localhost:18056/health   # gateway
docker compose -f deploy/compose/docker-compose.yml down -v
```

`.env.example` documents every variable (faithful to the 0.11 `deploy/compose` names: `DB_*`,
`REDIS_URL`, `ADMIN_TOKEN`, `INTERNAL_API_SECRET`, `MINIO_*`, `BROWSER_IMAGE`/`AGENT_IMAGE`,
`DOCKER_GID`, `*_HOST_PORT`).
