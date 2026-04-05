# Deployment


## Why

Vexa can be used without any deployment — the hosted service at [vexa.ai](https://vexa.ai) gives you an API key and you start sending bots immediately.

Self-hosting gives you control over your data and infrastructure. Three options, from simplest to most flexible.

## What

### Option 0: Hosted (no deployment)

Get an API key at [vexa.ai/dashboard/api-keys](https://vexa.ai/dashboard/api-keys). Start sending bots. No infrastructure needed.

### Option 1: Lite (easiest self-host)

Single Docker container. Needs external Postgres + transcription service.
See [lite/README.md](lite/README.md).

### Option 2: Docker Compose (development)

Full stack locally. All services, Postgres, Redis.
See [compose/README.md](compose/README.md) and the root Makefile: `make all`.

### Option 3: Helm (production K8s)

Two charts: `vexa` (full) and `vexa-lite` (single-pod).
See [helm/README.md](helm/README.md).

### Transcription service

All self-hosted deployments need a transcription service:

- **Ready to go:** Use Vexa transcription — sign up at [vexa.ai](https://vexa.ai), get a transcription API key. No GPU needed.
- **Self-host:** Run [services/transcription-service](../services/transcription-service/) on your own GPU for full data sovereignty.

## Image Tagging

Every build produces an immutable timestamp tag (`YYMMDD-HHMM`). Mutable tags (`dev`, `staging`, `latest`) are pointers updated only during publication — never during builds.

### Tag hierarchy


| Tag           | Created by             | Mutates?         | Purpose                            |
| ------------- | ---------------------- | ---------------- | ---------------------------------- |
| `YYMMDD-HHMM` | `make build`           | Never            | The actual identity of a build     |
| `dev`         | `make publish`         | Yes — re-pointed | Latest published development build |
| `staging`     | `make promote-staging` | Yes — re-pointed | Staging cluster deployment         |
| `latest`      | `make promote-latest`  | Yes — re-pointed | Production-ready release           |


### Registry

All images live on DockerHub under the `vexaai/` namespace:

- `vexaai/api-gateway`, `vexaai/admin-api`, `vexaai/meeting-api`, `vexaai/agent-api`, `vexaai/runtime-api`, `vexaai/mcp`, `vexaai/dashboard`, `vexaai/tts-service`, `vexaai/vexa-bot`

### Dev cycle

```bash
make build              # builds all images → vexaai/*:260330-1415 (saved to .last-tag)
make up                 # runs those exact images
make publish            # pushes to DockerHub + updates :dev pointer
make promote-staging    # re-points :staging → specific build
make promote-latest     # re-points :latest → specific build
```

During development, you always run images with a specific timestamp tag, so you know exactly what code is in each container.

## How

### Environment variables

One env-example covers both modes: [env/env-example](env/env-example)


| Variable                      | Required     | Description                                 |
| ----------------------------- | ------------ | ------------------------------------------- |
| `DASHBOARD_PATH`              | Compose only | Absolute path to vexa-dashboard checkout    |
| `TRANSCRIPTION_SERVICE_URL`   | Yes          | Transcription API endpoint                  |
| `TRANSCRIPTION_SERVICE_TOKEN` | If needed    | Auth token for transcription                |
| `LOCAL_TRANSCRIPTION`         | No           | Set `true` to run GPU transcription locally |
| `REMOTE_DB`                   | No           | Set `true` to use external Postgres         |
| `ADMIN_API_TOKEN`             | No           | Admin API auth token (default: `changeme`)  |


### Which mode?


| You want...                         | Use                                  |
| ----------------------------------- | ------------------------------------ |
| Use Vexa without deploying anything | Hosted at [vexa.ai](https://vexa.ai) |
| Easiest self-host                   | Lite                                 |
| Develop / contribute                | `make all` (Docker Compose)          |
| Production with scaling             | Helm                                 |


## Definition of Done

| # | Item | Weight | Status | Evidence | Last checked |
|---|------|--------|--------|----------|--------------|
| 1 | Lite option documented and working | 20 | PASS | Lite validated 2026-04-05, 14/14 services, transcription OK | 2026-04-05T18:50Z |
| 2 | Compose option documented and working | 20 | PASS | Compose validated in previous sessions, make all works | 2026-04-05 |
| 3 | Helm option documented | 10 | SKIP | Charts exist but not tested on cluster | — |
| 4 | Image tagging docs match Makefile | 15 | PASS | YYMMDD-HHMM tags confirmed in builds | 2026-04-05 |
| 5 | Transcription service options explained | 10 | PASS | Hosted + self-host both documented | 2026-04-05 |
| 6 | Environment variable reference complete | 15 | PARTIAL | env-example exists, lite vars now complete, compose vars need audit | 2026-04-05 |
| 7 | All registry image names correct | 10 | SKIP | Not verified this run | — |

## Confidence

Score: 65/100
Last validated: 2026-04-05 (full-stack-lite run)
Ceiling: Helm untested, compose env vars need audit, registry image names unverified

