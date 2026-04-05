---
id: test/infra-up
type: validation
requires: []
produces: [GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE]
validates: [infrastructure]
docs: [features/infrastructure/README.md, deploy/README.md]
mode: machine
---

# Infra Up

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Verify all services are running and healthy. First step in any validation pipeline.

This is a **dispatcher** — it detects the deployment mode and delegates to
the mode-specific procedure. Each mode has its own failure modes, checks,
and docs ownership.

```
Layer 1 (deployment-specific)
├── 01a-infra-compose.md  — docker compose, staging env, host ports from .env
├── 01b-infra-lite.md     — single container, supervisord, internal ports
└── 01c-infra-helm.md     — k8s, port-forward or ingress (TODO)

All three produce the same outputs:
  GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE
        │
        ▼
Layer 2 (deployment-agnostic) — same API, same tests
```

## Auto-detection

```bash
if docker ps --format '{{.Names}}' | grep -q '^vexa$'; then
    DEPLOY_MODE=lite
elif docker ps --filter name=vexa- --format '{{.Names}}' | grep -q vexa-; then
    DEPLOY_MODE=compose
elif kubectl get pods -l app=vexa -o name 2>/dev/null | grep -q pod/; then
    DEPLOY_MODE=helm
else
    echo "No deployment detected — start one first"
    exit 1
fi
```

## Dispatch

| Mode | Procedure | Container | GATEWAY_URL |
|------|-----------|-----------|-------------|
| compose | [01a-infra-compose.md](01a-infra-compose.md) | vexa-api-gateway-1 | http://localhost:8056 |
| lite | [01b-infra-lite.md](01b-infra-lite.md) | vexa | http://localhost:8056 |
| helm | 01c-infra-helm.md (TODO) | k8s pod | via ingress/port-forward |

Run the matching procedure. It produces the same outputs regardless of mode.

## Shared prerequisite

Transcription (GPU) runs as a separate stack — not managed by compose/lite/helm.
Verify it's running before starting any target:

```bash
curl -sf http://localhost:8085/health   # transcription-lb — must show gpu_available=true
```

## Outputs

| Name | Description |
|------|-------------|
| GATEWAY_URL | `http://localhost:{port}` (port depends on deploy mode) |
| ADMIN_URL | `http://localhost:{port}` |
| ADMIN_TOKEN | Admin API authentication token |
| DEPLOY_MODE | `compose`, `lite`, or `helm` |
