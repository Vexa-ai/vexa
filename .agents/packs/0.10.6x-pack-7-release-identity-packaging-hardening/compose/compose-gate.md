# Compose Gate

Status: machine pass; human eyeball pending

Validation used `docker compose -f deploy/compose/docker-compose.yml config` with pack-scoped identity and ports:

- `IMAGE_TAG=0.10.6.2.1`
- `VEXA_VERSION=0.10.6.2.1`
- `DASHBOARD_HOST_PORT=44460`
- `API_GATEWAY_HOST_PORT=44461`

The rendered config contains the dashboard image `vexaai/dashboard:0.10.6.2.1`, build arg `NEXT_PUBLIC_VEXA_OSS_VERSION: 0.10.6.2.1`, and the allocated non-default ports.

Human Compose blast-radius eyeball validation is required before PR-ready status. See `compose/human-eyeball.md`.

Evidence:

- `compose/compose-config.yaml`
- `ops/compose-config-render/stdout.log`
- `ops/compose-config-render/stderr.log`
